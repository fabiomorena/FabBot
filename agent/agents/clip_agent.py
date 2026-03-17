import re
import ipaddress
import httpx
from datetime import date
from pathlib import Path
from langchain_core.messages import SystemMessage, HumanMessage
from agent.audit import log_action
from agent.llm import get_llm

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


---
Halte dich kurz und präzise. Maximal 400 Wörter.
Antworte NUR mit dem Markdown, ohne Codeblock-Syntax.
"""


def _slugify(title: str) -> str:
    title = title.lower()
    title = re.sub(r"[äÄ]", "ae", title)
    title = re.sub(r"[öÖ]", "oe", title)
    title = re.sub(r"[üÜ]", "ue", title)
    title = re.sub(r"[ß]", "ss", title)
    title = re.sub(r"[^a-z0-9\s-]", "", title)
    title = re.sub(r"\s+", "-", title.strip())
    return title[:60]


def _is_ssrf_blocked(url: str) -> tuple[bool, str]:
    if not url.startswith(("http://", "https://")):
        return True, "Nur HTTP/HTTPS URLs erlaubt."
    try:
        host_match = re.match(r"https?://([^/:@\]]+|\[[^\]]+\])", url)
        if not host_match:
            return True, "URL konnte nicht geparst werden."
        host = host_match.group(1).strip("[]")
    except Exception:
        return True, "URL konnte nicht geparst werden."

    if host.lower() in ["localhost", "ip6-localhost"]:
        return True, f"Lokale URL nicht erlaubt: {host}"

    try:
        ip = ipaddress.ip_address(host)
        if any([ip.is_loopback, ip.is_private, ip.is_link_local,
                ip.is_multicast, ip.is_reserved, ip.is_unspecified]):
            return True, f"Nicht erlaubte IP-Adresse: {host}"
    except ValueError:
        for suffix in [".local", ".internal", ".localhost"]:
            if host.lower().endswith(suffix):
                return True, f"Lokaler Hostname nicht erlaubt: {host}"

    return False, ""


def _is_safe_output_path(path: Path) -> bool:
    """Prüft ob der Ausgabepfad innerhalb von KNOWLEDGE_DIR liegt.
    Verhindert Path-Traversal durch LLM-generierten Slug.
    """
    try:
        path.resolve().relative_to(KNOWLEDGE_DIR.resolve())
        return True
    except ValueError:
        return False


async def _fetch_url(url: str) -> str:
    blocked, reason = _is_ssrf_blocked(url)
    if blocked:
        raise ValueError(reason)

    async with httpx.AsyncClient(
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": "FabBot/1.0"},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        text = resp.text[:MAX_FETCH_SIZE]

    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_FETCH_SIZE]


async def clip_agent(url: str, chat_id: int) -> dict:
    url = url.strip()
    blocked, reason = _is_ssrf_blocked(url)
    if blocked:
        return {"ok": False, "error": reason}

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

    llm = get_llm()
    today = date.today().strftime("%d.%m.%Y")

    # ainvoke() statt invoke() – blockiert den asyncio Event-Loop nicht
    response = await llm.ainvoke([
        SystemMessage(content=SUMMARIZE_PROMPT),
        HumanMessage(content=f"URL: {url}\nDatum: {today}\n\nSeiteninhalt:\n{raw[:8000]}"),
    ])
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    content = content.strip()

    title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    title = title_match.group(1) if title_match else "notiz"
    slug = _slugify(title)
    filename = f"{date.today().isoformat()}-{slug}.md"

    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    file_path = KNOWLEDGE_DIR / filename

    # Path-Traversal-Schutz: Zielpfad muss innerhalb KNOWLEDGE_DIR liegen
    if not _is_safe_output_path(file_path):
        log_action("clip_agent", "write", f"path-traversal blocked: {file_path}", chat_id, status="blocked")
        return {"ok": False, "error": "Ungültiger Zielpfad – Schreiben verweigert."}

    preview_lines = [ln for ln in content.split("\n") if ln.strip()][:4]
    preview = "\n".join(preview_lines)

    return {
        "ok": True,
        "path": file_path,
        "content": content,
        "preview": preview,
        "filename": filename,
    }


def clip_agent_write(path: Path, content: str, chat_id: int) -> str:
    # Re-Validierung direkt vor dem Schreiben (TOCTOU-Schutz)
    if not _is_safe_output_path(path):
        log_action("clip_agent", "write", f"toctou-blocked: {path}", chat_id, status="blocked")
        return "Blockiert (Re-Validierung): Ungültiger Zielpfad."
    try:
        path.write_text(content, encoding="utf-8")
        log_action("clip_agent", "write", str(path), chat_id, status="executed")
        return f"Gespeichert: {path.name}"
    except Exception as e:
        return f"Fehler beim Speichern: {e}"