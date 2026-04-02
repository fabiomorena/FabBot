"""
Zentrales Protokoll-Modul für FabBot.
Alle Magic Strings für die interne Agent-Bot-Kommunikation sind hier definiert.
Niemals Rohstrings verwenden – immer diese Konstanten nutzen.
"""


class Proto:
    # Prefixes für HITL-Bestätigungen
    CONFIRM_TERMINAL      = "__CONFIRM_TERMINAL__:"
    CONFIRM_FILE_WRITE    = "__CONFIRM_FILE_WRITE__:"
    CONFIRM_CREATE_EVENT  = "__CONFIRM_CREATE_EVENT__:"
    CONFIRM_COMPUTER      = "__CONFIRM_COMPUTER__:"

    # Screenshot-Antwort
    SCREENSHOT            = "__SCREENSHOT__:"

    # Vision-Analyse Bestätigung
    CONFIRM_VISION        = "__CONFIRM_VISION__:"

    # Vision-Ergebnis – intern, wird an chat_agent weitergeleitet
    VISION_RESULT         = "__VISION_RESULT__:"

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
        return any([
            Proto.is_confirm_terminal(msg),
            Proto.is_confirm_file_write(msg),
            Proto.is_confirm_create_event(msg),
            Proto.is_confirm_computer(msg),
        ])