---
allowed-tools: Bash(git log:*), Bash(git add:*), Bash(git commit:*), Bash(git status:*), Bash(gh issue close:*), Read, Edit
description: Schließt eine FabBot-Phase ab – README-Eintrag, Commit mit Closes #XX, GitHub Issue schließen.
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

### 1. Phasen-Nummer
Nächste Nummer = gefundene Nummer + 1. Format: dreistellig (Phase 001, Phase 123).

### 2. README.md aktualisieren
Lies README.md und füge am Ende der Feature-Tabelle (vor der `---` Trennlinie nach der Tabelle) eine neue Zeile ein:
```
| ✅ | Phase NNN: <beschreibung> |
```

### 3. Commit
Stage README.md plus alle bereits modifizierten Dateien im Repo.

Commit-Message:
- Mit Issue: `feat: Phase NNN: <beschreibung> (Closes #XX)`
- Ohne Issue: `feat: Phase NNN: <beschreibung>`

Immer anhängen:
```
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

### 4. Issue schließen (nur wenn Issue-Nummer vorhanden)
```bash
gh issue close <issue#>
```

### 5. Ausgabe
Einzeilige Bestätigung: welche Phase, welcher Commit, welches Issue geschlossen.
