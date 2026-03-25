#!/bin/bash
LOG="$HOME/.fabbot/fabbot.log"
DATE=${1:-$(date +%Y-%m-%d)}

echo "=== FabBot Review: $DATE ==="
echo ""
echo "--- Fehler ---"
grep "$DATE" "$LOG" | grep -i "error\|ERROR\|exception" | sed 's/.*\[/[/'

echo ""
echo "--- Routing-Entscheidungen ---"
grep "$DATE" "$LOG" | grep -i "agent\|routing" | sed 's/.*\[/[/'

echo ""
echo "--- Anthropic API Calls ---"
grep "$DATE" "$LOG" | grep "api.anthropic.com/v1/messages" | wc -l | xargs echo "API Calls:"

echo ""
echo "--- Blockierte Anfragen ---"
grep "$DATE" "$LOG" | grep -i "block\|inject\|reject" | sed 's/.*\[/[/'
