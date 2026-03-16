import os
import re
import ipaddress
import httpx
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, AIMessage
from agent.state import AgentState
from agent.audit import log_action

llm = ChatAnthropic(
    model="claude-sonnet-4-20250514",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY")

MAX_FETCH_SIZE = 50_000
MAX_RESPONSE_LENGTH = 3000
TIMEOUT = 15


def _build_prompt() -> str:
    from datetime import date
    today = date.today().strftime("%d.%m.%Y")
    return f"""Du bist ein spezialisierter Web-Agent. Heutiges Datum: {today}

Analysiere die Anfrage und antworte NUR mit JSON:
{{
  "action": "search|fetch",
  "query": "Suchbegriff (bei search)",
  "url": "https://... (bei fetch)",
  "engine": "tavily|brave|auto"
}}

- action=search: Web-Suche nach Informationen
- action=fetch: Kompletten Inhalt einer URL abrufen
- engine=auto: Agent waehlt automatisch (Standard)
- Fuer aktuelle Nachrichten immer das aktuelle Jahr ({date.today().year}) in die Query einbauen

Kein Markdown, keine Erklaerung, nur reines JSON.
Wenn nicht unterstuetzt: UNSUPPORTED
"""


SUMMARIZE_PROMPT = """Du bist ein hilfreicher Assistent. 
Fasse die folgenden Web-Inhalte praezise und auf Deutsch zusammen.
Beantworte damit die urspruengliche Frage des Users.
Halte dich kurz – maximal 5-6 Saetze oder eine uebersichtliche Liste.
"""


def _extract_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _is_ssrf_blocked(url: str) -> tuple[bool, str]:
    """
    Prüft ob eine URL auf eine private/lokale Ressource zeigt (SSRF-Schutz).
    Blockt: localhost, private IPs, link-local (169.254.x.x), IPv6 loopback.
    Gibt (is_blocked, reason) zurück.
    """
    # Nur http/https erlaubt
    if not url.startswith(("http://", "https://")):
        return True, "Nur HTTP/HTTPS URLs erlaubt."

    # Hostname aus URL extrahieren
    try:
        # Einfacher Regex für Host-Extraktion (vor Port und Pfad)
        host_match = re.match(r"https?://([^/:@\]]+|\[[^\]]+\])", url)
        if not host_match:
            return True, "URL konnte nicht geparst werden."
        host = host_match.group(1).strip("[]")  # IPv6 brackets entfernen
    except Exception:
        return True, "URL konnte nicht geparst werden."

    # Bekannte Hostnamen direkt blockieren
    blocked_hostnames = ["localhost", "ip6-localhost", "ip6-loopback"]
    if host.lower() in blocked_hostnames:
        return True, f"Lokale URL ist nicht erlaubt: {host}"

    # IP-Adresse prüfen
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback:
            return True, f"Loopback-Adresse nicht erlaubt: {host}"
        if ip.is_private:
            return True, f"Private IP-Adresse nicht erlaubt: {host}"
        if ip.is_link_local:
            return True, f"Link-local Adresse nicht erlaubt (z.B. AWS Metadata): {host}"
        if ip.is_multicast:
            return True, f"Multicast-Adresse nicht erlaubt: {host}"
        if ip.is_reserved:
            return True, f"Reservierte IP-Adresse nicht erlaubt: {host}"
        if ip.is_unspecified:
            return True, f"Unspecified-Adresse nicht erlaubt: {host}"
    except ValueError:
        # Kein gültiges IP-Format → Hostname, String-Checks
        # Subdomains von lokalen Hosts blockieren (z.B. evil.localhost.attacker.com nicht,
        # aber metadata.internal o.ä. schon)
        blocked_suffixes = [".local", ".internal", ".localhost"]
        for suffix in blocked_suffixes:
            if host.lower().endswith(suffix):
                return True, f"Lokaler Hostname nicht erlaubt: {host}"

    return False, ""


async def _fetch_url(url: str) -> str:
    """Ruft den Inhalt einer URL ab."""
    blocked, reason = _is_ssrf_blocked(url)
    if blocked:
        return f"Blockiert: {reason}"

    async with httpx.AsyncClient(
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": "FabBot/1.0"},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "text" not in content_type and "json" not in content_type:
            return f"Fehler: Unterstuetzter Content-Type: {content_type}"

        text = resp.text[:MAX_FETCH_SIZE]

        # Einfaches HTML-Stripping
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        return text[:MAX_FETCH_SIZE]


async def _search_tavily(query: str) -> list[dict]:
    """Suche via Tavily API."""
    if not TAVILY_API_KEY:
        return []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "advanced",
                "max_results": 5,
                "include_raw_content": True,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])


async def _search_brave(query: str) -> list[dict]:
    """Suche via Brave Search API."""
    if not BRAVE_API_KEY:
        return []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 5, "search_lang": "de"},
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": BRAVE_API_KEY,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("web", {}).get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("description", ""),
            })
        return results


def _format_search_results(results: list[dict], source: str) -> str:
    """Formatiert Suchergebnisse als lesbaren Text."""
    if not results:
        return ""
    lines = [f"[{source} Ergebnisse]\n"]
    for i, r in enumerate(results[:5], 1):
        title = r.get("title", "")
        url = r.get("url", "")
        content = r.get("content") or r.get("raw_content", "")
        if content:
            content = content[:500]
        lines.append(f"{i}. {title}\n   {url}\n   {content}\n")
    return "\n".join(lines)


async def web_agent(state: AgentState) -> AgentState:
    import json

    messages = [SystemMessage(content=_build_prompt())] + state["messages"]
    response = llm.invoke(messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    content = _extract_json(content)

    if content == "UNSUPPORTED":
        return {"messages": [AIMessage(content="Diese Anfrage wird vom Web-Agent nicht unterstuetzt.")]}

    try:
        parsed = json.loads(content)
        action = parsed.get("action")
        query = parsed.get("query", "")
        url = parsed.get("url", "")
        engine = parsed.get("engine", "auto")
    except (json.JSONDecodeError, AttributeError) as e:
        return {"messages": [AIMessage(content=f"Fehler beim Parsen der Anfrage: {e}")]}

    try:
        if action == "fetch":
            if not url:
                return {"messages": [AIMessage(content="Keine URL angegeben.")]}

            # SSRF-Check vor dem Logging (kein geblocker URL im Log)
            blocked, reason = _is_ssrf_blocked(url)
            if blocked:
                log_action("web_agent", "fetch", f"ssrf-blocked: {reason}", state.get("telegram_chat_id"), status="blocked")
                return {"messages": [AIMessage(content=f"Blockiert: {reason}")]}

            log_action("web_agent", "fetch", url[:200], state.get("telegram_chat_id"), status="executed")
            raw = await _fetch_url(url)

            # Inhalt mit LLM zusammenfassen
            summary_messages = [
                SystemMessage(content=SUMMARIZE_PROMPT),
                *state["messages"],
                AIMessage(content=f"Seiteninhalt von {url}:\n\n{raw[:MAX_RESPONSE_LENGTH]}"),
            ]
            summary = llm.invoke(summary_messages)
            result = summary.content
            if isinstance(result, list):
                result = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in result)
            result = result.strip() or "Keine Zusammenfassung verfügbar."
            return {"messages": [AIMessage(content=result)]}

        elif action == "search":
            if not query:
                return {"messages": [AIMessage(content="Kein Suchbegriff angegeben.")]}

            log_action("web_agent", "search", query[:200], state.get("telegram_chat_id"), status="executed")

            raw_results = ""

            if engine in ("tavily", "auto") and TAVILY_API_KEY:
                results = await _search_tavily(query)
                if results:
                    raw_results = _format_search_results(results, "Tavily")

            # Brave als Fallback oder wenn explizit gewuenscht
            if (not raw_results or engine == "brave") and BRAVE_API_KEY:
                results = await _search_brave(query)
                if results:
                    raw_results += "\n" + _format_search_results(results, "Brave")

            if not raw_results:
                return {"messages": [AIMessage(content="Keine Suchergebnisse gefunden.")]}

            # Ergebnisse mit LLM zusammenfassen
            summary_messages = [
                SystemMessage(content=SUMMARIZE_PROMPT),
                *state["messages"],
                AIMessage(content=f"Suchergebnisse fuer '{query}':\n\n{raw_results[:MAX_RESPONSE_LENGTH]}"),
            ]
            summary = llm.invoke(summary_messages)
            result = summary.content
            if isinstance(result, list):
                result = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in result)
            result = result.strip() or "Keine Zusammenfassung verfügbar."
            return {"messages": [AIMessage(content=result)]}

        else:
            return {"messages": [AIMessage(content=f"Unbekannte Aktion: {action}")]}

    except httpx.HTTPStatusError as e:
        return {"messages": [AIMessage(content=f"HTTP Fehler: {e.response.status_code}")]}
    except httpx.TimeoutException:
        return {"messages": [AIMessage(content="Timeout beim Abrufen der Webseite.")]}
    except Exception as e:
        return {"messages": [AIMessage(content=f"Fehler: {e}")]}