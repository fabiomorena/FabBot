---
name: phase
description: Schließt eine FabBot-Phase ab: README-Eintrag, Commit mit Closes #XX, GitHub-Issue schließen. Aufruf: /phase <issue#> "<beschreibung>" oder /phase "<beschreibung>" (ohne Issue).
---

Du bist ein Automatisierungs-Assistent für FabBot-Phasen-Abschlüsse.

## Aufgabe

Der User ruft `/phase` mit einer Beschreibung auf, optional mit einer GitHub-Issue-Nummer.

Argumente: `$ARGUMENTS`

Parse die Argumente:
- Wenn erstes Argument eine Zahl ist → Issue-Nummer, Rest = Beschreibung
- Sonst → nur Beschreibung, kein Issue

## Schritte (in dieser Reihenfolge)

### 1. Nächste Phasen-Nummer ermitteln

Führe aus:
```bash
git log --oneline | grep -oE 'Phase [0-9]+' | grep -oE '[0-9]+' | sort -n | tail -1
```
Nächste Nummer = gefundene Nummer + 1.

### 2. README.md – Feature-Table aktualisieren

Füge am Ende der Feature-Tabelle (vor der `---` Trennlinie) eine neue Zeile ein:
```
| ✅ | Phase NNN: <beschreibung> |
```

### 3. Commit erstellen

Commit-Message-Format:
- Mit Issue: `feat: Phase NNN: <beschreibung> (Closes #XX)`
- Ohne Issue: `feat: Phase NNN: <beschreibung>`

Füge immer hinzu:
```
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

Staged files: nur `README.md` plus alle anderen bereits geänderten/gestageten Dateien im Repo.

### 4. GitHub-Issue schließen (nur wenn Issue-Nummer vorhanden)

```bash
gh issue close <issue#>
```

### 5. Bestätigung ausgeben

Kurze Zusammenfassung: Phase-Nummer, was committed wurde, welches Issue geschlossen.

## Wichtig

- Phasen-Nummer immer dreistellig mit führenden Nullen: Phase 001, Phase 123
- Beschreibung direkt aus den Argumenten übernehmen, nicht umformulieren
- Wenn kein Issue angegeben: Schritt 4 überspringen
- Keine Rückfragen – direkt ausführen
