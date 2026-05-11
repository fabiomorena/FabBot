"""
agent/proactive/curator.py – Phase 181 (Issue #143)

Background Curator: wöchentliche Profil-Konsolidierung mit Dry-Run + manuellem Review.

Flow:
  1. should_run() prüft Idle >= 2h + letzter Lauf >= 7d (+ nicht muted)
  2. run_dry_run() → LLM analysiert Profil, baut Proposal, speichert in State, gibt Report zurück
  3. User bestätigt via /curator apply (oder /curator cancel)
  4. apply_pending() schreibt Profil via write_profile() mit expected_base_hash

Regeln:
  - Archivieren statt löschen (archived-Block im YAML)
  - _pinned: true Items werden nie angepasst (Pre-Filter + Defense-Check)
  - Pending Proposal verfällt nach 24h
  - Bei STALE (Profil zwischenzeitlich geändert) → Proposal invalidieren
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATE_FILE = Path.home() / ".fabbot" / "curator_state.json"
_MEMORY_DB = Path.home() / ".fabbot" / "memory.db"
_FABBOT_LOG = Path.home() / ".fabbot" / "fabbot.log"
_IDLE_THRESHOLD = 2 * 3600  # 2 Stunden
_COOLDOWN_DAYS = 7  # Mindesttakt zwischen Läufen
_PROPOSAL_TTL = 24 * 3600  # Proposal verfällt nach 24h
_LLM_TIMEOUT = 60.0
_YAML_MAX_CHARS = 8000


# ---------------------------------------------------------------------------
# State-Management
# ---------------------------------------------------------------------------


def _load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(data: dict) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Idle-Detection
# ---------------------------------------------------------------------------


def get_idle_seconds() -> float:
    """Sekunden seit letzter User-Aktivität (via mtime von memory.db)."""
    try:
        if _MEMORY_DB.exists():
            mtime = _MEMORY_DB.stat().st_mtime
            return (datetime.now(timezone.utc).timestamp()) - mtime
        # Fallback: Log-Datei
        if _FABBOT_LOG.exists():
            return (datetime.now(timezone.utc).timestamp()) - _FABBOT_LOG.stat().st_mtime
    except Exception as e:
        logger.debug(f"curator idle_seconds Fehler: {e}")
    return 0.0


# ---------------------------------------------------------------------------
# Trigger-Logik
# ---------------------------------------------------------------------------


def should_run(*, force: bool = False) -> bool:
    """True wenn Curator-Dry-Run gestartet werden soll."""
    if not force:
        if get_idle_seconds() < _IDLE_THRESHOLD:
            return False
        from agent.proactive.heartbeat import is_muted

        if is_muted():
            return False
    state = _load_state()
    last_run = state.get("last_run_at")
    if last_run and not force:
        try:
            dt = datetime.fromisoformat(last_run)
            if (datetime.now(timezone.utc) - dt).total_seconds() < _COOLDOWN_DAYS * 86400:
                return False
        except ValueError:
            pass
    return True


# ---------------------------------------------------------------------------
# Hilfs-Funktionen
# ---------------------------------------------------------------------------


def _filter_pinned(obj: Any, path: str = "") -> list[str]:
    """Gibt Pfade aller _pinned: true Items zurück (rekursiv)."""
    pinned: list[str] = []
    if isinstance(obj, dict):
        if obj.get("_pinned") is True:
            pinned.append(path)
        for k, v in obj.items():
            pinned.extend(_filter_pinned(v, f"{path}.{k}" if path else k))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            pinned.extend(_filter_pinned(item, f"{path}[{i}]"))
    return pinned


def _remove_pinned_from_input(profile: dict) -> dict:
    """Erstellt eine tiefe Kopie des Profils ohne _pinned-Metadaten für den LLM-Input."""

    def _strip(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: _strip(v) for k, v in obj.items() if k != "_pinned"}
        if isinstance(obj, list):
            return [_strip(item) for item in obj]
        return obj

    return _strip(profile)


def _truncate_profile_yaml(profile: dict) -> str:
    """Kürzt Profil-YAML auf _YAML_MAX_CHARS, immer an Top-Level-Sektionsgrenzen."""
    import yaml

    full = yaml.dump(profile, allow_unicode=True, default_flow_style=False, sort_keys=False)
    if len(full) <= _YAML_MAX_CHARS:
        return full

    sections: list[str] = []
    total = 0
    added_keys = 0
    for key, value in profile.items():
        chunk = yaml.dump({key: value}, allow_unicode=True, default_flow_style=False, sort_keys=False)
        if total + len(chunk) > _YAML_MAX_CHARS:
            if not sections:  # erste Sektion bereits zu groß → hard truncate
                sections.append(chunk[:_YAML_MAX_CHARS])
                added_keys = 1
            break
        sections.append(chunk)
        total += len(chunk)
        added_keys += 1
    skipped = len(profile) - added_keys
    if skipped:
        sections.append(f"# ... [{skipped} Sektionen gekürzt]\n")
    return "".join(sections)


def _get_pinned_paths(profile: dict) -> set[str]:
    return set(_filter_pinned(profile))


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# LLM-Analyse
# ---------------------------------------------------------------------------

_ANALYZER_PROMPT = """\
Du bist ein Profil-Kurator. Analysiere das folgende persönliche YAML-Profil eines Users.
Identifiziere:
1. Duplikate (zwei Einträge die dieselbe Person/Sache beschreiben)
2. Veraltete Einträge (nicht mehr relevant, längst abgeschlossen, überholt)
3. Redundante Notizen (mehrere Notes die dasselbe aussagen)
4. Merge-Vorschläge (zwei Einträge die sinnvoll zusammengeführt werden könnten)

KRITISCHE REGELN – Verstöße machen die Ausgabe ungültig:
- index, indices und keep_index sind IMMER ganzzahlige Integer (0, 1, 2, ...) – niemals Strings, Schlüssel oder Bezeichnungen
- section ist IMMER ein einfacher Schlüssel oder Pfad wie "notes", "people", "projects.active" – niemals mit Leerzeichen oder "+"
- Schlage NUR echte Probleme vor – lieber zu wenig als zu viel
- Einträge mit _pinned: true NIEMALS anfassen
- Archivieren statt löschen
- Antworte NUR mit validem JSON, kein Text davor/danach

Profil:
{profile_yaml}

Antworte mit diesem JSON-Schema:
{{
  "duplicates": [
    {{"section": "people", "indices": [2, 7], "reason": "...", "keep_index": 2, "merged_entry": {{...}}}}
  ],
  "stale": [
    {{"section": "projects.active", "index": 4, "reason": "kein Update seit 6 Monaten"}}
  ],
  "redundant_notes": [
    {{"indices": [12, 18], "reason": "...", "keep_index": 12}}
  ],
  "summary": "Kurze Zusammenfassung was gefunden wurde (1-2 Sätze)"
}}

Wenn nichts gefunden wurde, gib leere Arrays zurück.
"""


async def _analyze_profile(profile: dict) -> dict | None:
    """Ruft Sonnet auf und gibt strukturierte Analyse zurück. None bei Fehler."""
    try:
        from langchain_core.messages import HumanMessage
        from agent.llm import get_llm

        profile_yaml = _truncate_profile_yaml(profile)

        llm = get_llm()
        prompt = _ANALYZER_PROMPT.format(profile_yaml=profile_yaml)
        response = await asyncio.wait_for(
            llm.ainvoke([HumanMessage(content=prompt)]),
            timeout=_LLM_TIMEOUT,
        )
        content = response.content
        if isinstance(content, list):
            content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        content = content.strip()

        # JSON aus Response extrahieren (LLM gibt manchmal ```json ... ``` zurück)
        if "```" in content:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                content = content[start:end]

        analysis = json.loads(content)
        return _sanitize_analysis(analysis)
    except json.JSONDecodeError as e:
        logger.warning(f"curator _analyze_profile: LLM-Antwort ist kein valides JSON: {e}")
        return None
    except asyncio.TimeoutError:
        logger.warning("curator _analyze_profile: LLM-Timeout")
        return None
    except Exception as e:
        logger.warning(f"curator _analyze_profile Fehler: {e}")
        return None


def _sanitize_analysis(analysis: dict) -> dict:
    """Filtert LLM-Ausgabe: entfernt Einträge mit ungültigen Indizes (Strings, None, negativ)."""

    def valid_idx(v) -> bool:
        try:
            return int(v) >= 0
        except (ValueError, TypeError):
            return False

    def valid_section(s) -> bool:
        return isinstance(s, str) and " " not in s and "+" not in s and len(s) > 0

    stale = [e for e in analysis.get("stale", []) if valid_section(e.get("section", "")) and valid_idx(e.get("index"))]
    duplicates = [
        e
        for e in analysis.get("duplicates", [])
        if valid_section(e.get("section", ""))
        and all(valid_idx(i) for i in e.get("indices", []))
        and valid_idx(e.get("keep_index", 0))
    ]
    redundant_notes = [
        e for e in analysis.get("redundant_notes", []) if all(valid_idx(i) for i in e.get("indices", []))
    ]

    dropped = (
        len(analysis.get("stale", []))
        - len(stale)
        + len(analysis.get("duplicates", []))
        - len(duplicates)
        + len(analysis.get("redundant_notes", []))
        - len(redundant_notes)
    )
    if dropped:
        logger.warning(f"curator _sanitize_analysis: {dropped} ungültige LLM-Einträge herausgefiltert.")

    return {**analysis, "stale": stale, "duplicates": duplicates, "redundant_notes": redundant_notes}


# ---------------------------------------------------------------------------
# Proposal-Builder
# ---------------------------------------------------------------------------


def _build_proposal(profile: dict, analysis: dict) -> dict:
    """
    Berechnet das Ziel-Profil aus dem Analyse-Ergebnis.
    Items werden nie gelöscht – sie landen im archived-Block.
    _pinned Items werden verteidigt (Defense-Check).
    """
    import copy

    target = copy.deepcopy(profile)
    operations: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    if "archived" not in target or not isinstance(target["archived"], list):
        target["archived"] = []

    archived_list: list[dict] = target["archived"]

    # Veraltete Einträge archivieren
    for stale in analysis.get("stale", []):
        section = stale.get("section", "")
        idx_raw = stale.get("index")
        reason = stale.get("reason", "veraltet")
        if idx_raw is None or not section:
            continue
        try:
            idx = int(idx_raw)
        except (ValueError, TypeError):
            continue

        # Navigation durch verschachtelte Sektionen (z.B. "projects.active")
        parts = section.split(".")
        obj = target
        try:
            for part in parts:
                obj = obj[part]
            if not isinstance(obj, list) or idx >= len(obj):
                continue
            item = obj[idx]
            # Defense: _pinned prüfen
            if isinstance(item, dict) and item.get("_pinned"):
                logger.warning(f"curator: _pinned-Item in stale ignoriert: {section}[{idx}]")
                continue
            base = item if isinstance(item, dict) else {"_value": item}
            archived_list.append(
                {**base, "_archived_at": now_iso, "_archived_reason": reason, "_archived_from": section}
            )
            obj.pop(idx)
            operations.append({"type": "archive", "section": section, "index": idx, "reason": reason})
        except (KeyError, TypeError, IndexError) as e:
            logger.warning(f"curator build_proposal stale navigation Fehler: {e}")

    # Redundante Notes
    notes = target.get("notes", [])
    if isinstance(notes, list):
        for rn in sorted(
            analysis.get("redundant_notes", []), key=lambda x: _safe_int(x.get("keep_index")), reverse=True
        ):
            indices = [_safe_int(i, -1) for i in rn.get("indices", []) if i is not None]
            indices = [i for i in indices if i >= 0]
            keep_index_raw = rn.get("keep_index", indices[0] if indices else None)
            keep_index = _safe_int(keep_index_raw, -1) if keep_index_raw is not None else None
            reason = rn.get("reason", "redundant")
            for idx in sorted(indices, reverse=True):
                if idx == keep_index or idx >= len(notes):
                    continue
                item = notes[idx]
                archived_list.append(
                    {"_note": item, "_archived_at": now_iso, "_archived_reason": reason, "_archived_from": "notes"}
                )
                notes.pop(idx)
                operations.append({"type": "archive_note", "index": idx, "reason": reason})

    # Duplikate: keep_index behalten, andere archivieren + optional mergen
    for dup in analysis.get("duplicates", []):
        section = dup.get("section", "")
        raw_indices = dup.get("indices", [])
        indices = [_safe_int(i, -1) for i in raw_indices if i is not None]
        indices = [i for i in indices if i >= 0]
        keep_index_raw = dup.get("keep_index", indices[0] if indices else None)
        keep_index = _safe_int(keep_index_raw, -1) if keep_index_raw is not None else None
        merged_entry = dup.get("merged_entry")
        reason = dup.get("reason", "Duplikat")
        if not section or not indices:
            continue

        parts = section.split(".")
        obj = target
        try:
            for part in parts:
                obj = obj[part]
            if not isinstance(obj, list):
                continue
            for idx in sorted(indices, reverse=True):
                if idx >= len(obj):
                    continue
                item = obj[idx]
                if isinstance(item, dict) and item.get("_pinned"):
                    logger.warning(f"curator: _pinned-Item in duplicates ignoriert: {section}[{idx}]")
                    continue
                if idx != keep_index:
                    archived_list.append(
                        {**item, "_archived_at": now_iso, "_archived_reason": reason, "_archived_from": section}
                    )
                    obj.pop(idx)
                    operations.append({"type": "archive_duplicate", "section": section, "index": idx, "reason": reason})
            # Merged Entry anwenden wenn angegeben
            if merged_entry and keep_index is not None and keep_index < len(obj):
                if not obj[keep_index].get("_pinned"):
                    obj[keep_index] = {**obj[keep_index], **merged_entry}
                    operations.append({"type": "merge", "section": section, "index": keep_index, "reason": reason})
        except (KeyError, TypeError, IndexError) as e:
            logger.warning(f"curator build_proposal duplicates navigation Fehler: {e}")

    return {
        "target_profile": target,
        "operations": operations,
        "summary": analysis.get("summary", ""),
        "created_at": now_iso,
    }


# ---------------------------------------------------------------------------
# Report-Formatter
# ---------------------------------------------------------------------------


def format_report(proposal: dict, expires_at: str) -> str:
    """Baut den Telegram-Markdown-Report für den Dry-Run."""
    ops = proposal.get("operations", [])
    summary = proposal.get("summary", "")
    created = proposal.get("created_at", "")[:10]

    lines = [f"*Curator Dry-Run ({created})*"]
    if summary:
        lines.append(f"_{summary}_")
    lines.append("")

    archives = [o for o in ops if o["type"] == "archive"]
    dups = [o for o in ops if o["type"] in ("archive_duplicate", "merge")]
    notes = [o for o in ops if o["type"] == "archive_note"]

    if not ops:
        lines.append("Nichts zu konsolidieren – Profil ist sauber.")
        return "\n".join(lines)

    if archives:
        lines.append(f"*Veraltet ({len(archives)}):*")
        for o in archives:
            lines.append(f"  • {o['section']}[{o['index']}] – {o['reason']}")

    if dups:
        lines.append(f"*Duplikate ({len(dups)}):*")
        for o in dups:
            lines.append(f"  • {o['section']}[{o.get('index', '?')}] – {o['reason']}")

    if notes:
        lines.append(f"*Redundante Notizen ({len(notes)}):*")
        for o in notes:
            lines.append(f"  • notes[{o['index']}] – {o['reason']}")

    expires_short = expires_at[:16].replace("T", " ")
    lines.append("")
    lines.append(f"Bestätigen: `/curator apply` (gültig bis {expires_short} UTC)")
    lines.append("Verwerfen: `/curator cancel`")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dry-Run-Orchestrator
# ---------------------------------------------------------------------------


async def run_dry_run(*, force: bool = False) -> str | None:
    """
    Führt Dry-Run durch: lädt Profil, analysiert via LLM, baut Proposal, speichert State.
    Gibt Report-String zurück oder None bei leerem Profil / LLM-Fehler.
    force=True ignoriert Idle-Check (für manuellen /curator dryrun).
    """
    report, _ = await _debug_dry_run(force=force)
    return report


async def _debug_dry_run(*, force: bool = False) -> tuple[str | None, str]:
    """Wie run_dry_run, gibt zusätzlich Debug-Info zurück: (report, debug_msg)."""
    from agent.profile import load_profile_with_hash

    profile, base_hash = load_profile_with_hash()
    if not profile:
        logger.info("curator run_dry_run: Profil leer – übersprungen.")
        return None, "Profil ist leer oder konnte nicht geladen werden."

    analysis = await _analyze_profile(_remove_pinned_from_input(profile))
    if analysis is None:
        return None, "LLM-Analyse fehlgeschlagen (Timeout, Auth oder JSON-Fehler – siehe Log)."

    proposal = _build_proposal(profile, analysis)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=_PROPOSAL_TTL)).isoformat()

    ops = proposal.get("operations", [])
    ops_hash = hashlib.md5(json.dumps(ops, sort_keys=True).encode(), usedforsecurity=False).hexdigest()

    state = _load_state()
    if not force and state.get("last_reported_ops_hash") == ops_hash:
        state["last_dry_run_at"] = datetime.now(timezone.utc).isoformat()
        _save_state(state)
        logger.info(f"curator Dry-Run: {len(ops)} Operationen – unverändert, kein Report.")
        return None, "Operationen unverändert seit letztem Report – übersprungen."

    state["pending_proposal"] = proposal
    state["pending_base_hash"] = base_hash
    state["pending_expires_at"] = expires_at
    state["last_dry_run_at"] = datetime.now(timezone.utc).isoformat()
    state["last_reported_ops_hash"] = ops_hash
    _save_state(state)

    logger.info(f"curator Dry-Run abgeschlossen: {len(ops)} Operationen vorgeschlagen.")
    return format_report(proposal, expires_at), "ok"


# ---------------------------------------------------------------------------
# Apply / Cancel
# ---------------------------------------------------------------------------


async def apply_pending() -> tuple[bool, str]:
    """
    Wendet pending Proposal an. Gibt (success, message) zurück.
    """
    from agent.profile import write_profile, WriteResult

    state = _load_state()
    proposal = state.get("pending_proposal")
    base_hash = state.get("pending_base_hash")
    expires_at = state.get("pending_expires_at")

    if not proposal:
        return False, "Kein offener Curator-Vorschlag. Erst /curator dryrun ausführen."

    if expires_at:
        try:
            if datetime.now(timezone.utc) > datetime.fromisoformat(expires_at):
                _invalidate_pending(state)
                return False, "Vorschlag abgelaufen. Bitte /curator dryrun erneut ausführen."
        except ValueError:
            pass

    target = proposal.get("target_profile")
    if not target:
        _invalidate_pending(state)
        return False, "Ungültiger Vorschlag (kein target_profile). /curator dryrun erneut."

    # Defense-Check: keine _pinned-Items verändert
    original_pinned = _get_pinned_paths(target)
    if original_pinned:
        logger.warning(f"curator apply: _pinned-Paths im target_profile gefunden: {original_pinned}")

    result = await write_profile(target, expected_base_hash=base_hash)

    if result == WriteResult.OK:
        state["last_run_at"] = datetime.now(timezone.utc).isoformat()
        _invalidate_pending(state)
        ops_count = len(proposal.get("operations", []))
        return True, f"Profil konsolidiert. {ops_count} Operation(en) angewendet."

    if result == WriteResult.STALE:
        _invalidate_pending(state)
        return False, "Profil wurde zwischenzeitlich geändert. Dry-Run ist veraltet – bitte /curator dryrun erneut."

    _invalidate_pending(state)
    return False, f"Schreiben fehlgeschlagen ({result.value}). Kein Profil-Update."


def cancel_pending() -> str:
    """Verwirft pending Proposal."""
    state = _load_state()
    if not state.get("pending_proposal"):
        return "Kein offener Vorschlag vorhanden."
    _invalidate_pending(state)
    return "Curator-Vorschlag verworfen."


def _invalidate_pending(state: dict) -> None:
    state.pop("pending_proposal", None)
    state.pop("pending_base_hash", None)
    state.pop("pending_expires_at", None)
    _save_state(state)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def get_status() -> str:
    """Gibt Status-Übersicht für /curator status zurück."""
    state = _load_state()
    lines = ["*Curator Status*"]

    last_run = state.get("last_run_at")
    lines.append(f"Letzter Lauf: {last_run[:16].replace('T', ' ') + ' UTC' if last_run else 'noch nie'}")

    last_dry = state.get("last_dry_run_at")
    lines.append(f"Letzter Dry-Run: {last_dry[:16].replace('T', ' ') + ' UTC' if last_dry else 'noch nie'}")

    idle = get_idle_seconds()
    lines.append(f"Idle seit: {int(idle / 60)} Minuten")

    pending = state.get("pending_proposal")
    if pending:
        expires_at = state.get("pending_expires_at", "")
        ops_count = len(pending.get("operations", []))
        try:
            expired = datetime.now(timezone.utc) > datetime.fromisoformat(expires_at)
        except (ValueError, TypeError):
            expired = False
        status = "ABGELAUFEN" if expired else "offen"
        lines.append(f"Pending Proposal: {ops_count} Operationen ({status})")
        if not expired:
            lines.append("  → /curator apply oder /curator cancel")
    else:
        lines.append("Pending Proposal: keiner")

    return "\n".join(lines)
