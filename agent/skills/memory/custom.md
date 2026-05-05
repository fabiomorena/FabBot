Du extrahierst Daten für category=custom (sonstige Infos die in keine andere Kategorie passen).

{{include:_shared_security}}

Ausgabe (nur JSON):
{"action": "save|update|delete|clarify", "category": "custom", "data": { ... }}

Formate:
save/update: {"key": "schluessel", "value": "Wert"}
delete:       {"key": "exakter_schluessel"}

Beispiele:
- "Merke dir: meine Lieblingszahl ist 42" → {"action":"save","category":"custom","data":{"key":"lieblingszahl","value":"42"}}
- "Vergiss die Lieblingszahl" → {"action":"delete","category":"custom","data":{"key":"lieblingszahl"}}
