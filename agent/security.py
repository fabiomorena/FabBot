import re
import time
import unicodedata
import logging
from collections import OrderedDict

logger = logging.getLogger(__name__)

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

DANGEROUS_SHELL_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+~",
    r"mkfs",
    r"dd\s+if=",
    r">\s*/dev/sd",
    r"chmod\s+777\s+/",
    r"sudo\s+rm",
    r":\s*\(\s*\)\s*\{.*:\s*\|.*:.*&.*\}",  # Fork bomb
    r"curl\s+.*\|\s*(bash|sh|zsh)",
    r"wget\s+.*\|\s*(bash|sh|zsh)",
]

MAX_INPUT_LENGTH = 2000

RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW = 60

# Bounded OrderedDict – verhindert unbegrenztes Wachstum bei User-ID-Flooding.
# Maximal RATE_LIMIT_DICT_SIZE Einträge; älteste werden bei Überschreitung entfernt.
RATE_LIMIT_DICT_SIZE = 10_000
_rate_limit: OrderedDict[int, list[float]] = OrderedDict()

_HOMOGLYPH_MAP = str.maketrans({
    # Kyrillisch → ASCII
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
    "х": "x", "у": "y", "А": "A", "В": "B", "Е": "E",
    "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "Х": "X",
    # Griechisch → ASCII
    "α": "a", "β": "b", "ο": "o", "ρ": "p",
    # Fullwidth
    "ａ": "a", "ｂ": "b", "ｃ": "c", "ｄ": "d", "ｅ": "e",
    "ｉ": "i", "ｊ": "j", "ｋ": "k", "ｌ": "l", "ｍ": "m",
    "ｎ": "n", "ｏ": "o", "ｐ": "p", "ｑ": "q", "ｒ": "r",
    "ｓ": "s", "ｔ": "t", "ｕ": "u", "ｖ": "v", "ｗ": "w",
    "ｘ": "x", "ｙ": "y", "ｚ": "z",
})


def _normalize(text: str) -> str:
    """Normalisiert Unicode auf ASCII-Basis um homoglyph-Bypässe zu verhindern."""
    text = text.translate(_HOMOGLYPH_MAP)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def check_rate_limit(user_id: int) -> bool:
    """Gibt True zurück wenn User im erlaubten Bereich liegt, False wenn geblockt.
    Verwendet ein bounded OrderedDict um Memory-Flooding zu verhindern.
    """
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    # Bounded size: ältesten Eintrag entfernen wenn Dict zu gross wird
    if user_id not in _rate_limit:
        if len(_rate_limit) >= RATE_LIMIT_DICT_SIZE:
            _rate_limit.popitem(last=False)  # ältesten Eintrag entfernen
        _rate_limit[user_id] = []
    else:
        # Zur MRU-Position verschieben
        _rate_limit.move_to_end(user_id)

    # Alte Einträge außerhalb des Fensters entfernen
    _rate_limit[user_id] = [t for t in _rate_limit[user_id] if t > window_start]

    if len(_rate_limit[user_id]) >= RATE_LIMIT_MAX:
        logger.warning(f"Rate limit exceeded: user_id={user_id}")
        return False

    _rate_limit[user_id].append(now)
    return True


def sanitize_input(text: str, user_id: int | None = None) -> tuple[bool, str]:
    """
    Prüft und bereinigt User-Input.
    Gibt (is_safe, reason_or_clean_text) zurück.
    """
    if not text or not text.strip():
        return False, "Leere Eingabe."

    if len(text) > MAX_INPUT_LENGTH:
        return False, f"Eingabe zu lang (max. {MAX_INPUT_LENGTH} Zeichen)."

    if user_id is not None and not check_rate_limit(user_id):
        return False, f"Zu viele Nachrichten. Bitte {RATE_LIMIT_WINDOW}s warten."

    text_normalized = _normalize(text.lower())

    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text_normalized, re.IGNORECASE):
            logger.warning(f"Prompt Injection erkannt: pattern='{pattern}' input='{text[:100]}'")
            return False, "Ungültige Eingabe erkannt."

    for pattern in DANGEROUS_SHELL_PATTERNS:
        if re.search(pattern, text_normalized, re.IGNORECASE):
            logger.warning(f"Gefährliches Shell-Muster erkannt: pattern='{pattern}' input='{text[:100]}'")
            return False, "Gefährlicher Befehl erkannt."

    clean = text.replace("\x00", "").strip()
    return True, clean