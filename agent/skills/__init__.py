"""Skill-Loader für FabBot – lädt Markdown-Skill-Dateien pro Domain/Kategorie."""

import functools
from pathlib import Path

_SKILLS_DIR = Path(__file__).parent


@functools.lru_cache(maxsize=64)
def load_skill(domain: str, name: str) -> str:
    """Lädt agent/skills/{domain}/{name}.md und löst {{include:X}}-Marker auf."""
    path = _SKILLS_DIR / domain / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Skill nicht gefunden: {domain}/{name}.md")
    content = path.read_text(encoding="utf-8")
    return _resolve_includes(domain, content)


def _resolve_includes(domain: str, content: str) -> str:
    import re

    def replacer(match: re.Match) -> str:
        include_name = match.group(1)
        include_path = _SKILLS_DIR / domain / f"{include_name}.md"
        if not include_path.exists():
            raise FileNotFoundError(f"Include nicht gefunden: {domain}/{include_name}.md")
        return include_path.read_text(encoding="utf-8")

    return re.sub(r"\{\{include:([^}]+)\}\}", replacer, content)
