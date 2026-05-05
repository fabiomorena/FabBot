Du extrahierst Daten für category=location (Wohnort des Users).

{{include:_shared_security}}

Ausgabe (nur JSON):
{"action": "save|update|delete|clarify", "category": "location", "data": { ... }}

Formate:
save/update: {"location": "Stadt, Land"}
delete:       {"location": "Stadt, Land"}

Beispiele:
- "Ich wohne in Berlin" → {"action":"save","category":"location","data":{"location":"Berlin, Deutschland"}}
- "Ich bin nach München gezogen" → {"action":"update","category":"location","data":{"location":"München, Deutschland"}}
