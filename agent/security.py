import re
import logging

logger = logging.getLogger(__name__)

# Bekannte Prompt-Injection-Muster
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
    r"vergiss\s+(alle\s+)?(vorherigen|obigen)\s+(anweisungen|befehle)",
    r"you\s+are\s+now",
    r"du\s+bist\s+jetzt\s+(ein\s+)?(neuer|anderer|böser)",
    r"act\s+as\s+(if\s+you\s+are|a)",
    r"jailbreak",
    r"system\s*prompt",
    r"<\s*system\s*>",
    r"\[INST\]",
    r"###\s*(instruction|system|prompt)",
    r"override\s+(all\s+)?(safety|security|restrictions)",
    r"disable\s+(all\s+)?(safety|restrictions|filters)",
]

# Gefährliche Shell-Muster die niemals erlaubt sind
DANGEROUS_SHELL_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+~",
    r"mkfs",
    r"dd\s+if=",
    r">\s*/dev/sd",
    r"chmod\s+777\s+/",
    r"sudo\s+rm",
    r":\(\)\{:\|:&\};:",  # Fork bomb
    r"curl\s+.*\|\s*(bash|sh|zsh)",
    r"wget\s+.*\|\s*(bash|sh|zsh)",
]

MAX_INPUT_LENGTH = 2000


def sanitize_input(text: str) -> tuple[bool, str]:
    """
    Prüft und bereinigt User-Input.
    Gibt (is_safe, reason_or_clean_text) zurück.
    """
    if not text or not text.strip():
        return False, "Leere Eingabe."

    if len(text) > MAX_INPUT_LENGTH:
        return False, f"Eingabe zu lang (max. {MAX_INPUT_LENGTH} Zeichen)."

    text_lower = text.lower()

    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            logger.warning(f"Prompt Injection erkannt: pattern='{pattern}' input='{text[:100]}'")
            return False, "Ungültige Eingabe erkannt."

    for pattern in DANGEROUS_SHELL_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            logger.warning(f"Gefährliches Shell-Muster erkannt: pattern='{pattern}' input='{text[:100]}'")
            return False, "Gefährlicher Befehl erkannt."

    # Einfache Bereinigung: Null-Bytes entfernen
    clean = text.replace("\x00", "").strip()
    return True, clean
