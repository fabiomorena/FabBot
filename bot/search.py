import os
import re
from pathlib import Path

KNOWLEDGE_DIR = Path(os.getenv("KNOWLEDGE_DIR", str(Path.home() / "Documents" / "Wissen")))
MAX_RESULTS = 5


def _extract_meta(content: str, filepath: Path) -> dict:
    """Extrahiert Titel, Tags und Zusammenfassung aus einer Markdown-Notiz."""
    title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else filepath.stem

    tags_match = re.search(r"\*\*Tags:\*\*\s*(.+)$", content, re.MULTILINE)
    tags = tags_match.group(1).strip() if tags_match else ""

    source_match = re.search(r"\*\*Quelle:\*\*\s*(.+)$", content, re.MULTILINE)
    source = source_match.group(1).strip() if source_match else ""

    summary_match = re.search(r"##\s+Zusammenfassung\s*\n(.+?)(?=\n##|\Z)", content, re.DOTALL)
    summary = summary_match.group(1).strip()[:200] if summary_match else ""

    return {
        "title": title,
        "tags": tags,
        "source": source,
        "summary": summary,
        "filename": filepath.name,
    }


def search_knowledge(query: str) -> str:
    """
    Durchsucht alle .md Dateien in ~/Documents/Wissen nach dem Query.
    Sucht in Titel, Tags, Zusammenfassung und Kernpunkten.
    Gibt formatierten Text für Telegram zurück.
    """
    if not KNOWLEDGE_DIR.exists():
        return "Wissens-Ordner nicht gefunden. Speichere zuerst eine Notiz mit /clip."

    files = sorted(KNOWLEDGE_DIR.glob("*.md"), reverse=True)
    if not files:
        return "Noch keine Notizen gespeichert. Nutze /clip <URL> um eine zu speichern."

    query_lower = query.lower().strip()

    # Tag-Suche: /search #Iran
    is_tag_search = query_lower.startswith("#")
    search_term = query_lower.lstrip("#")

    matches = []
    for filepath in files:
        try:
            content = filepath.read_text(encoding="utf-8")
        except Exception:
            continue

        # Suche im gesamten Inhalt
        if is_tag_search:
            # Nur in Tags-Zeile suchen
            tags_match = re.search(r"\*\*Tags:\*\*\s*(.+)$", content, re.MULTILINE | re.IGNORECASE)
            if not tags_match or search_term not in tags_match.group(1).lower():
                continue
        else:
            if search_term not in content.lower():
                continue

        meta = _extract_meta(content, filepath)
        matches.append(meta)

        if len(matches) >= MAX_RESULTS:
            break

    if not matches:
        return f"Keine Notizen gefunden für: *{query}*"

    lines = [f"*{len(matches)} Ergebnis{'se' if len(matches) > 1 else ''}* für _{query}_\n"]
    for i, m in enumerate(matches, 1):
        lines.append(f"*{i}. {m['title']}*")
        if m["summary"]:
            lines.append(m["summary"])
        if m["tags"]:
            lines.append(m["tags"])
        if m["source"]:
            lines.append(m["source"])
        lines.append("")

    return "\n".join(lines).strip()


def list_knowledge() -> str:
    """Listet alle gespeicherten Notizen auf."""
    if not KNOWLEDGE_DIR.exists() or not list(KNOWLEDGE_DIR.glob("*.md")):
        return "Noch keine Notizen gespeichert. Nutze /clip <URL> um eine zu speichern."

    files = sorted(KNOWLEDGE_DIR.glob("*.md"), reverse=True)
    lines = [f"*{len(files)} gespeicherte Notiz{'en' if len(files) > 1 else ''}:*\n"]

    for filepath in files[:20]:
        try:
            content = filepath.read_text(encoding="utf-8")
            meta = _extract_meta(content, filepath)
            lines.append(f"• {meta['title']}")
            if meta["tags"]:
                lines.append(f"  {meta['tags']}")
        except Exception:
            lines.append(f"• {filepath.stem}")

    if len(files) > 20:
        lines.append(f"\n... und {len(files) - 20} weitere")

    return "\n".join(lines)
