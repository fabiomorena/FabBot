Du extrahierst Daten für category=event (einmalige Ereignisse, abgeschlossene Handlungen).

{{include:_shared_security}}

Ausgabe (nur JSON):
{"action": "save", "category": "event", "data": { ... }}

Format:
save: {"description": "kurze Beschreibung des Ereignisses", "date": "YYYY-MM-DD", "tags": ["tag1", "tag2"]}
delete: {"description": "Teilbeschreibung des zu löschenden Eintrags"}

Regeln:
- description: prägnante Kurzbeschreibung, max. 80 Zeichen
- date: nur wenn explizit genannt oder eindeutig aus Kontext ableitbar (z.B. "heute" → aktuelles Datum aus {current_datetime}); sonst weglassen
- tags: 1-3 kurze Schlagwörter (Kategorie, Ort, Thema); sonst leeres Array

Beispiele:
- "Ich habe heute das Zugticket nach Kassel gekauft" → {"action":"save","category":"event","data":{"description":"Zugticket nach Kassel gekauft","date":"HEUTE","tags":["reise","kassel","ticket"]}}
- "Ich habe das Projekt abgeschlossen" → {"action":"save","category":"event","data":{"description":"Projekt abgeschlossen","tags":["projekt"]}}
- "Ich war gestern beim Zahnarzt" → {"action":"save","category":"event","data":{"description":"Zahnarzttermin","tags":["gesundheit"]}}
