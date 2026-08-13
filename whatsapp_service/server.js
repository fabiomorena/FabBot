'use strict';

/**
 * FabBot WhatsApp Service – Phase 83
 *
 * Express HTTP-Server der whatsapp-web.js kapselt.
 * Läuft auf localhost:8767, gesichert mit Bearer-Token.
 *
 * Status-Datei: ~/.fabbot/wa_ready
 *   – wird beim 'ready' Event erstellt
 *   – wird beim 'disconnected' / Prozess-Ende gelöscht
 *   – Python liest diese Datei synchron via is_session_ready()
 *
 * Endpoints:
 *   GET  /status  → {ok, ready, qr_available, error}
 *   GET  /qr      → {ok, qr}  oder 404
 *   POST /send    → {to, message} → {ok, detail|error}
 */

const { Client, LocalAuth } = require('whatsapp-web.js');
const express = require('express');
const os      = require('os');
const path    = require('path');
const fs      = require('fs');

// ── Konfiguration ────────────────────────────────────────────────────────
const PORT        = parseInt(process.env.WA_SERVICE_PORT || '8767', 10);
const TOKEN       = process.env.FABBOT_WA_TOKEN;
const DATA_PATH   = path.join(os.homedir(), '.fabbot', 'whatsapp_wwebjs');
const STATUS_FILE = path.join(os.homedir(), '.fabbot', 'wa_ready');

// ── Logging ───────────────────────────────────────────────────────────────
// Mit Zeitstempel, seit die Ausgabe in ~/.fabbot/whatsapp_service.log landet:
// ohne ihn ist nicht erkennbar, wie lange der Client in einem Zustand hängt.
function ts() {
    // Lokalzeit, nicht UTC – sonst lassen sich die Einträge nicht mit
    // ~/.fabbot/fabbot.log korrelieren, das in Lokalzeit schreibt.
    const d = new Date();
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} `
         + `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function log(...args) {
    console.log(`${ts()} [FabBot-WA]`, ...args);
}

function logErr(...args) {
    console.error(`${ts()} [FabBot-WA]`, ...args);
}

if (!TOKEN) {
    logErr('FABBOT_WA_TOKEN nicht gesetzt – beende.');
    process.exit(1);
}

// ── Express ───────────────────────────────────────────────────────────────
const app = express();
app.use(express.json());

function requireAuth(req, res, next) {
    if (req.headers['authorization'] !== `Bearer ${TOKEN}`) {
        return res.status(401).json({ ok: false, error: 'Unauthorized' });
    }
    next();
}

// ── State ─────────────────────────────────────────────────────────────────
let currentQR  = null;
let isReady    = false;
let lastError  = null;

function setStatusFile(exists) {
    try {
        if (exists) {
            fs.mkdirSync(path.dirname(STATUS_FILE), { recursive: true });
            fs.writeFileSync(STATUS_FILE, '1');
        } else {
            fs.unlinkSync(STATUS_FILE);
        }
    } catch (_) {}
}

// ── WhatsApp Client ───────────────────────────────────────────────────────
const client = new Client({
    authStrategy: new LocalAuth({ dataPath: DATA_PATH }),
    puppeteer: {
        headless: true,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
        ],
    },
});

client.on('qr', (qr) => {
    currentQR = qr;
    isReady   = false;
    setStatusFile(false);
    log('QR-Code bereit – bitte /wa_setup in Telegram ausführen.');
});

client.on('authenticated', () => {
    log('Authentifiziert.');
    currentQR = null;
});

// Zwischen 'authenticated' und 'ready' lädt der Client Chats und Kontakte.
// Genau dort blieb der Service am 12./13.08.2026 stehen – ohne diese beiden
// Handler war im Log nur zu sehen, DASS 'ready' ausblieb, nicht woran.
client.on('loading_screen', (percent, message) => {
    log(`Laden: ${percent}% – ${message}`);
});

client.on('change_state', (state) => {
    log('Zustandswechsel:', state);
});

client.on('ready', () => {
    isReady   = true;
    currentQR = null;
    lastError = null;
    setStatusFile(true);
    log('Bereit – WhatsApp verbunden.');
});

client.on('auth_failure', (msg) => {
    lastError = `Auth fehlgeschlagen: ${msg}`;
    isReady   = false;
    setStatusFile(false);
    logErr('Auth-Fehler:', msg);
});

client.on('disconnected', (reason) => {
    isReady   = false;
    currentQR = null;
    setStatusFile(false);
    log('Getrennt:', reason);
});

// Cleanup bei Prozess-Ende
function cleanup() {
    setStatusFile(false);
    process.exit(0);
}
process.on('SIGTERM', cleanup);
process.on('SIGINT',  cleanup);

client.initialize().catch((err) => {
    logErr('Initialize fehlgeschlagen:', err.message);
    lastError = err.message;
});

// ── Routes ────────────────────────────────────────────────────────────────

app.get('/status', requireAuth, (req, res) => {
    res.json({
        ok:           true,
        ready:        isReady,
        qr_available: currentQR !== null,
        error:        lastError,
    });
});

app.get('/qr', requireAuth, (req, res) => {
    if (!currentQR) {
        return res.status(404).json({ ok: false, error: 'Kein QR-Code verfügbar.' });
    }
    res.json({ ok: true, qr: currentQR });
});

app.post('/send', requireAuth, async (req, res) => {
    const { to, message } = req.body || {};

    if (!to || !message) {
        return res.status(400).json({ ok: false, error: '"to" und "message" sind erforderlich.' });
    }
    if (message.length > 4096) {
        return res.status(400).json({ ok: false, error: 'Nachricht zu lang (max 4096 Zeichen).' });
    }
    if (!isReady) {
        return res.status(503).json({ ok: false, error: 'WhatsApp nicht verbunden.' });
    }

    try {
        const contacts = await client.getContacts();
        const toTrim   = to.trim().replace(/[ ]?\(Du\)[ ]?$/i, '').trim();
        const toLow    = toTrim.toLowerCase();

        // Kontakt-Suche: exakter Match bevorzugt, dann case-insensitive, dann partial
        let found =
            contacts.find(c => c.name       && c.name.trim()       === toTrim) ||
            contacts.find(c => c.pushname   && c.pushname.trim()   === toTrim) ||
            contacts.find(c =>
                (c.name     && c.name.trim().toLowerCase()     === toLow) ||
                (c.pushname && c.pushname.trim().toLowerCase() === toLow)
            );

        if (!found) {
            const partialMatches = contacts.filter(c =>
                (c.name     && c.name.trim().toLowerCase().includes(toLow)) ||
                (c.pushname && c.pushname.trim().toLowerCase().includes(toLow))
            );
            if (partialMatches.length === 1) {
                found = partialMatches[0];
            } else if (partialMatches.length > 1) {
                const names = partialMatches.map(c => c.name || c.pushname).join(', ');
                return res.json({ ok: false, error: `Mehrdeutiger Kontakt '${to}'. Meintest du: ${names}?` });
            }
        }

        if (!found) {
            return res.json({
                ok:    false,
                error: `Kontakt '${to}' nicht in WhatsApp gefunden. Überprüfe den WhatsApp-Anzeigenamen.`,
            });
        }

        await client.sendMessage(found.id._serialized, message);
        const displayName = found.name || found.pushname || to;
        res.json({ ok: true, detail: `✅ Gesendet an ${displayName}` });

    } catch (err) {
        logErr('Send-Fehler:', err.message);
        res.json({ ok: false, error: err.message || 'Unbekannter Fehler beim Senden.' });
    }
});

// ── Start ─────────────────────────────────────────────────────────────────
app.listen(PORT, '127.0.0.1', () => {
    log(`HTTP-Server läuft auf http://127.0.0.1:${PORT}`);
});
