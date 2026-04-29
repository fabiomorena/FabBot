---
allowed-tools: Bash(git log:*), Bash(git add:*), Bash(git commit:*), Bash(git push:*), Bash(git status:*), Bash(git checkout:*), Bash(git pull:*), Bash(gh issue close:*), Bash(gh pr create:*), Bash(gh pr merge:*), Bash(gh pr checks:*), Bash(gh run list:*), Bash(git diff:*), Bash(grep:*), Bash(launchctl:*), Bash(pytest:*), Read, Edit
description: Schließt eine FabBot-Phase ab – Security-Check, README-Eintrag, Commit, PR-Workflow mit CI, Bot-Neustart.
---

## Argumente
$ARGUMENTS

Parse die Argumente:
- Wenn erstes Argument eine Zahl ist → Issue-Nummer, der Rest = Beschreibung
- Sonst → nur Beschreibung, kein Issue

## Kontext
- Höchste bisherige Phasen-Nummer: !`git log --oneline | grep -oE 'Phase [0-9]+' | grep -oE '[0-9]+' | sort -n | tail -1`
- Aktueller Git-Status: !`git status --short`

## Schritte

### 0. Security-Check (vor dem Commit)

Prüfe die geänderten Dateien mit: `git diff HEAD --name-only`

Scanne die geänderten `.py`-Dateien auf folgende Muster (grep):
- Hardcodierte API-Keys: `sk-`, `AIza`, `ANTHROPIC_API_KEY\s*=\s*"`, `api_key\s*=\s*"[^{]`
- Hardcodierte Passwörter: `password\s*=\s*"`, `secret\s*=\s*"`
- Shell-Injection-Risiken in terminal_agent: direkte f-String-Interpolation in `subprocess.run()` oder `os.system()`
- Sensible Daten in Logs: `logging.*password`, `logging.*token`, `logging.*api_key`

Wenn ein Treffer gefunden wird:
- **STOP** – zeige den Treffer und frage den User ob er trotzdem fortfahren will
- Kein automatisches Commit bei kritischen Funden

Wenn alles sauber: kurze Bestätigung „Security-Check OK" und weiter.

### 0.5 Tests
Führe die Test-Suite aus:
```bash
pytest --tb=short -q
```
- Wenn alle Tests grün: kurze Bestätigung „Tests OK (X passed)" und weiter.
- Wenn Tests fehlschlagen: **STOP** – zeige die fehlgeschlagenen Tests und frage ob trotzdem fortgefahren werden soll.

### 1. Phasen-Nummer
Nächste Nummer = gefundene Nummer + 1. Format: dreistellig (Phase 001, Phase 123).

### 2. README.md aktualisieren
Lies README.md und füge nach der letzten `Phase NNN`-Zeile (vor der `---` Trennlinie) eine neue Zeile ein:
```
- **Phase NNN** ✅ <beschreibung> – <details>; X tests green
```

### 3. Feature-Branch erstellen
```bash
git checkout -b phase/NNN-<slug>
```
`<slug>` = kebab-case Kurzform der Beschreibung, max. 40 Zeichen.

### 4. Commit
Stage README.md plus alle bereits modifizierten Dateien im Repo.

Commit-Message:
- Mit Issue: `feat: Phase NNN: <beschreibung> (Closes #XX)`
- Ohne Issue: `feat: Phase NNN: <beschreibung>`

Immer anhängen:
```
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

### 5. Issue schließen (nur wenn Issue-Nummer vorhanden)
```bash
gh issue close <issue#>
```

### 6. Push + PR erstellen
```bash
git push -u origin phase/NNN-<slug>
```

Dann PR erstellen:
```bash
gh pr create \
  --title "feat: Phase NNN – <beschreibung>" \
  --body "## Änderungen\n<bullet-list>\n\n## Tests\nX passed, 0 failures"
```

Auto-Merge aktivieren:
```bash
gh pr merge --auto --merge <PR-Nummer>
```
Falls Auto-Merge nicht verfügbar (Exit-Code ≠ 0): manuell mit `gh pr merge --merge <PR-Nummer>` nach CI.

### 7. CI abwarten
```bash
gh pr checks <PR-Nummer> --watch
```
- Wenn alle Checks grün: kurze Bestätigung und weiter.
- Wenn ein Check rot: **STOP** – zeige den Fehler und warte auf User-Entscheidung.

Nach erfolgreichem CI zurück auf master:
```bash
git checkout master && git pull
```

### 8. Bot-Neustart
Starte den Bot via launchd neu damit die neuen Änderungen aktiv werden:
```bash
launchctl kickstart -k gui/$(id -u)/com.fabbot.agent
```
Warte 3 Sekunden, dann Status prüfen:
```bash
launchctl list com.fabbot.agent
```
- Wenn PID vorhanden: „Bot neu gestartet ✓"
- Wenn kein PID: „Bot-Neustart fehlgeschlagen – bitte manuell prüfen"

### 9. Ausgabe
Einzeilige Bestätigung: welche Phase, welcher Commit, PR-URL, welches Issue geschlossen, Bot-Status.
