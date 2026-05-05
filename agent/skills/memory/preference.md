Du extrahierst Daten für category=preference.

{{include:_shared_security}}

Ausgabe (nur JSON):
{"action": "save|update|delete|clarify", "category": "preference", "data": { ... }}

Formate:
save/update: {"key": "schluessel", "value": "Wert", "subcategory": "entertainment|lifestyle|tech|work|persoenlich"}
delete:       {"key": "exakter_schluessel_aus_profil"}
clarify:      {"question": "Meinst du X oder Y?", "options": ["dotted.path.1", "dotted.path.2"]}

subcategory-Wahl:
- entertainment: Lieblingsfilm, -serie, Musik-Genre, Spiele
- lifestyle: Sport, Ernährung, Hobbys, Schlafgewohnheiten
- tech: Editor, Programmiersprache, OS, Tools
- work: Arbeitsmethoden, Arbeitszeiten, Fokus-Präferenzen
- persoenlich: Charaktereigenschaften, Werte, persönliche Fakten

WICHTIG bei delete:
- Schau in den Profil-Kontext unten.
- Wenn der User einen Wert nennt (z.B. "Star Trek"), such den zugehörigen Key im Profil.
- Verwende IMMER den exakten Key aus dem Profil, nicht den genannten Wert.
- Wenn mehrere Keys passen → action=clarify mit options-Liste.
- Wenn kein Treffer → key = genannter Begriff (Fallback).

Beispiele:
- "Mein Lieblingseditor ist Zed" → {"action":"save","category":"preference","data":{"key":"lieblingseditor","value":"Zed","subcategory":"tech"}}
- "Ich mache dreimal die Woche Sport" → {"action":"save","category":"preference","data":{"key":"sport_frequenz","value":"3x pro Woche","subcategory":"lifestyle"}}
