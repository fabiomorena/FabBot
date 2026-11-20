"""
bot/flug/bereinigung.py – Phase 247 (Issue #385)

Bereinigt Texte aus externen APIs, bevor sie in Telegram-Nachrichten oder
Logs landen. Telegram verlinkt URLs auch im Klartext automatisch – ein
Zeilenumbruch oder eine Domain in einem Ortsnamen würde sonst eine klickbare
fremde Zeile in einem Preisalarm erzeugen.
"""

import re

# Zeilen-/Absatztrenner, Zero-Width- und Bidi-Steuerzeichen. Zur Laufzeit aus
# Codepoints gebaut: die Zeichen selbst im Quelltext wären genau die
# Trojan-Source-Klasse, gegen die dieses Modul schützt (bandit B613).
_UNSICHTBAR = "".join(
    chr(c) for c in (0x2028, 0x2029, *range(0x200B, 0x2010), *range(0x202A, 0x202F), *range(0x2066, 0x206A))
)
_STEUERZEICHEN = re.compile("[" + re.escape("".join(chr(c) for c in (*range(0x20), 0x7F)) + _UNSICHTBAR) + "]")
_LINKARTIG = re.compile(r"://|www\.|[a-z0-9-]+\.[a-z]{2,}(?:/|$)", re.IGNORECASE)


def einzeilig(text: object, max_laenge: int) -> str:
    """Ein Zeilenumbruch- und steuerzeichenfreier, gekürzter Text."""
    return " ".join(_STEUERZEICHEN.sub(" ", str(text)).split())[:max_laenge]


def sieht_aus_wie_link(text: str) -> bool:
    return bool(_LINKARTIG.search(text))
