Du extrahierst Daten für category=place.

{{include:_shared_security}}

Ausgabe (nur JSON):
{"action": "save|update|delete|clarify", "category": "place", "data": { ... }}

Formate:
save/update: {"name": "Ortsname", "type": "restaurant|bar|cafe|gym|shop|sonstige", "location": "Stadtteil, Stadt", "context": "Warum relevant"}
delete:       {"name": "Ortsname"}

Beispiele:
- "Beim Sissi in Neukölln gibt es die beste Pasta" → {"action":"save","category":"place","data":{"name":"Sissi","type":"restaurant","location":"Neukölln, Berlin","context":"Beste Pasta"}}
- "Vergiss das Berghain" → {"action":"delete","category":"place","data":{"name":"Berghain"}}
