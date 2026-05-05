Du extrahierst Daten für category=project (eigene Software-Projekte des Users).

{{include:_shared_security}}

Ausgabe (nur JSON):
{"action": "save|update|delete|clarify", "category": "project", "data": { ... }}

Formate:
save/update: {"name": "Projektname", "description": "Kurze Beschreibung", "stack": ["Python", "..."], "priority": "high|medium|low"}
delete:       {"name": "Projektname"}

Beispiele:
- "Ich arbeite an FabBot, ein Telegram-Bot in Python" → {"action":"save","category":"project","data":{"name":"FabBot","description":"Telegram-Bot","stack":["Python"],"priority":"medium"}}
- "Lösch das Projekt AudioSynth" → {"action":"delete","category":"project","data":{"name":"AudioSynth"}}
