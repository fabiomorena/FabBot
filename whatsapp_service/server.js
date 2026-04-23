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

if (!TOKEN) {
    console.error('[FabBot-WA] FABBOT_WA_TOKEN nicht gesetzt – beende.');
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
    console.log('[FabBot-WA] QR-Code bereit – bitte /wa_setup in Telegram ausführen.');
});

client.on('authenticated', () => {
    console.log('[FabBot-WA] Authentifiziert.');
    currentQR = null;
});

client.on('ready', () => {
    isReady   = true;
    currentQR = null;
    lastError = null;
    setStatusFile(true);
    console.log('[FabBot-WA] Bereit – WhatsApp verbunden.');
});

client.on('auth_failure', (msg) => {
    lastError = `Auth fehlgeschlagen: ${msg}`;
    isReady   = false;
    setStatusFile(false);
    console.error('[FabBot-WA] Auth-Fehler:', msg);
});

client.on('disconnected', (reason) => {
    isReady   = false;
    currentQR = null;
    setStatusFile(false);
    console.log('[FabBot-WA] Getrennt:', reason);
});

// Cleanup bei Prozess-Ende
function cleanup() {
    setStatusFile(false);
    process.exit(0);
}
process.on('SIGTERM', cleanup);
process.on('SIGINT',  cleanup);

client.initialize().catch((err) => {
    console.error('[FabBot-WA] Initialize fehlgeschlagen:', err.message);
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
        const toTrim   = to.trim().replace(/\s*\(Du\)\s*$/i, '').trim();
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
        console.error('[FabBot-WA] Send-Fehler:', err.message);
        res.json({ ok: false, error: err.message || 'Unbekannter Fehler beim Senden.' });
    }
});

// ── Start ─────────────────────────────────────────────────────────────────
app.listen(PORT, '127.0.0.1', () => {
    console.log(`[FabBot-WA] HTTP-Server läuft auf http://127.0.0.1:${PORT}`);
});
