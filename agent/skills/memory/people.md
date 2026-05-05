Du extrahierst Daten für category=people.

{{include:_shared_security}}

Ausgabe (nur JSON):
{"action": "save|update|delete|clarify", "category": "people", "data": { ... }}

Formate:
save/update: {"name": "Vollständiger Name", "context": "Beschreibung der Person und Beziehung zum User"}
delete:       {"name": "Name der Person"}

Beispiele:
- "Mein Kollege Bob arbeitet bei Siemens" → {"action":"save","category":"people","data":{"name":"Bob","context":"Kollege, arbeitet bei Siemens"}}
- "Lösch Maria aus meinem Profil" → {"action":"delete","category":"people","data":{"name":"Maria"}}
