"""
Zentrales Protokoll-Modul für FabBot.

Phase 226 (Issue #274): HITL läuft vollständig über LangGraph interrupt() –
die CONFIRM_*-Magic-Strings und is_confirm_*/is_any_confirm wurden entfernt.
Übrig bleiben SCREENSHOT (Screenshot-Dispatch) und MEMORY_VISION_MARKER.
"""


class Proto:
    # Screenshot-Antwort (einziger verbleibender Dispatch-Marker)
    SCREENSHOT = "__SCREENSHOT__:"

    # Phase 91: Kennzeichnet Vision-Memory-Messages die im State sichtbar bleiben sollen.
    # Single Source of Truth in protocol.py (genutzt in supervisor._filter_hitl_messages()).
    MEMORY_VISION_MARKER = "Bildbeschreibung"

    @staticmethod
    def is_screenshot(msg: str) -> bool:
        return msg.startswith(Proto.SCREENSHOT)
