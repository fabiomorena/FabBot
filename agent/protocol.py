"""
Zentrales Protokoll-Modul für FabBot.

Phase 91 Fixes:
- Proto.MEMORY_VISION_MARKER: Magic String "Bildbeschreibung" aus supervisor.py extrahiert.
  Vorher hardcoded in _filter_hitl_messages() – bei Format-Änderung stille Regression.
- Proto.is_any_confirm(): CONFIRM_VISION ergänzt (war vergessen).
  Vorher: Vision-Confirmations wurden von is_any_confirm() nicht erkannt.
"""


class Proto:
    # Prefixes für HITL-Bestätigungen
    CONFIRM_TERMINAL = "__CONFIRM_TERMINAL__:"
    CONFIRM_FILE_WRITE = "__CONFIRM_FILE_WRITE__:"
    CONFIRM_CREATE_EVENT = "__CONFIRM_CREATE_EVENT__:"
    CONFIRM_COMPUTER = "__CONFIRM_COMPUTER__:"
    CONFIRM_WHATSAPP = "__CONFIRM_WHATSAPP__:"

    # Screenshot-Antwort
    SCREENSHOT = "__SCREENSHOT__:"

    # Vision-Analyse Bestätigung
    CONFIRM_VISION = "__CONFIRM_VISION__:"

    # Vision-Ergebnis – intern, wird an chat_agent weitergeleitet
    VISION_RESULT = "__VISION_RESULT__:"

    # Phase 91: Kennzeichnet Vision-Memory-Messages die im State sichtbar bleiben sollen.
    # Vorher: "Bildbeschreibung" hardcoded in supervisor._filter_hitl_messages() –
    # Format-Änderung hätte die Filterlogik still gebrochen.
    # Jetzt: Single Source of Truth in protocol.py.
    MEMORY_VISION_MARKER = "Bildbeschreibung"

    @staticmethod
    def is_confirm_whatsapp(msg: str) -> bool:
        return msg.startswith(Proto.CONFIRM_WHATSAPP)

    @staticmethod
    def is_confirm_terminal(msg: str) -> bool:
        return msg.startswith(Proto.CONFIRM_TERMINAL)

    @staticmethod
    def is_confirm_file_write(msg: str) -> bool:
        return msg.startswith(Proto.CONFIRM_FILE_WRITE)

    @staticmethod
    def is_confirm_create_event(msg: str) -> bool:
        return msg.startswith(Proto.CONFIRM_CREATE_EVENT)

    @staticmethod
    def is_confirm_computer(msg: str) -> bool:
        return msg.startswith(Proto.CONFIRM_COMPUTER)

    @staticmethod
    def is_screenshot(msg: str) -> bool:
        return msg.startswith(Proto.SCREENSHOT)

    @staticmethod
    def is_confirm_vision(msg: str) -> bool:
        return msg.startswith(Proto.CONFIRM_VISION)

    @staticmethod
    def is_any_confirm(msg: str) -> bool:
        return any(
            [
                Proto.is_confirm_terminal(msg),
                Proto.is_confirm_file_write(msg),
                Proto.is_confirm_create_event(msg),
                Proto.is_confirm_computer(msg),
                Proto.is_confirm_whatsapp(msg),
                Proto.is_confirm_vision(msg),  # Phase 91: war vergessen
            ]
        )
