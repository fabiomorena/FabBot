import os
import re
import httpx
from datetime import date
from pathlib import Path
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage
from agent.audit import log_action

llm = ChatAnthropic(
    model="claude-sonnet-4-20250514",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

KNOWLEDGE_DIR = Path.home() / "Documents" / "Wissen"
MAX_FETCH_SIZE = 50_000
TIMEOUT = 15

SUMMARIZE_PROMPT = """Du bist ein Wissensmanager. Erstelle aus dem folgenden Webseiteninhalt
eine strukturierte Markdown-Notiz auf Deutsch.

Format:
# [Aussagekräftiger Titel]

**Quelle:** [URL]
**Datum:** [Datum]
**Tags:** #tag1 #tag2 #tag3

## Zusammenfassung
2-3 Sätze was das Dokument behandelt.

## Kernpunkte
- Wichtigster Punkt
- Nächster Punkt
- ...

## Notizen
Raum für eigene Gedanken (leer lassen).

---
Halte dich kurz und präzise. Maximal 400 Wörter.
Antworte NUR mit dem Markdown, ohne Codeblock-Syntax.
"""


def _slugify(title: str) -> str:
    """Wandelt einen Titel in einen dateisicheren Slug um."""
    title = title.lower()
    title = re.sub(r"[äÄ]", "ae", title)
    title = re.sub(r"[öÖ]", "oe", title)
    title = re.sub(r"[üÜ]", "ue", title)
    title = re.sub(r"[ß]", "ss", title)
    title = re.sub(r"[^a-z0-9\s-]", "", title)
    title = re.sub(r"\s+", "-", title.strip())
    return title[:60]


async def _fetch_url(url: str) -> str:
    """Ruft den Inhalt einer URL ab und strippt HTML."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("Nur HTTP/HTTPS URLs erlaubt.")

    blocked = ["localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.", "172.16."]
    for b in blocked:
        if b in url:
            raise ValueError("Lokale URLs sind nicht erlaubt.")

    async with httpx.AsyncClient(
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": "FabBot/1.0"},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        text = resp.text[:MAX_FETCH_SIZE]

    # HTML strippen
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_FETCH_SIZE]


async def clip_agent(url: str, chat_id: int) -> dict:
    """
    Fetcht eine URL, erstellt eine Markdown-Notiz und gibt
    Pfad + Inhalt zurück für HITL-Bestätigung in bot.py.

    Returns:
        {"ok": True, "path": Path, "content": str, "preview": str}
        {"ok": False, "error": str}
    """
    # URL validieren
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "Ungültige URL. Muss mit http:// oder https:// beginnen."}

    log_action("clip_agent", "fetch", url[:200], chat_id, status="executed")

    try:
        raw = await _fetch_url(url)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"HTTP Fehler: {e.response.status_code}"}
    except httpx.TimeoutException:
        return {"ok": False, "error": "Timeout beim Abrufen der Seite."}
    except Exception as e:
        return {"ok": False, "error": f"Fehler: {e}"}

    if not raw.strip():
        return {"ok": False, "error": "Seite enthält keinen lesbaren Text."}

    # Mit LLM zusammenfassen
    today = date.today().strftime("%d.%m.%Y")
    response = llm.invoke([
        SystemMessage(content=SUMMARIZE_PROMPT),
        HumanMessage(content=f"URL: {url}\nDatum: {today}\n\nSeiteninhalt:\n{raw[:8000]}"),
    ])
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    content = content.strip()

    # Titel aus erstem H1 extrahieren für Dateiname
    title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    title = title_match.group(1) if title_match else "notiz"
    slug = _slugify(title)
    filename = f"{date.today().isoformat()}-{slug}.md"

    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    file_path = KNOWLEDGE_DIR / filename

    # Preview für HITL (erste 3 Zeilen)
    preview_lines = [l for l in content.split("\n") if l.strip()][:4]
    preview = "\n".join(preview_lines)

    return {
        "ok": True,
        "path": file_path,
        "content": content,
        "preview": preview,
        "filename": filename,
    }


def clip_agent_write(path: Path, content: str, chat_id: int) -> str:
    """Wird nach HITL-Bestätigung aufgerufen."""
    try:
        path.write_text(content, encoding="utf-8")
        log_action("clip_agent", "write", str(path), chat_id, status="executed")
        return f"Gespeichert: {path.name}"
    except Exception as e:
        return f"Fehler beim Speichern: {e}"
