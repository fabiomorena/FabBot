"""
bot/flug/modelle.py – Phase 247 (Issue #385)

Unveränderliche Datenmodelle des Flug-Trackers. Änderungen laufen über
`model_copy(update=...)`, nie über Zuweisung.
"""

from datetime import date, datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

MIN_PREIS = 10
MAX_PREIS = 5000
MAX_NAECHTE = 60


class Ort(BaseModel):
    """Stadt oder Flughafen mit IATA-Code."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(pattern=r"^[A-Z]{3}$")
    name: str = Field(min_length=1, max_length=80)
    land: str = Field(default="", max_length=80)

    def anzeige(self) -> str:
        return f"{self.name} ({self.code})"


class Angebot(BaseModel):
    """Ein Preis aus dem Travelpayouts-Cache."""

    model_config = ConfigDict(frozen=True)

    preis: int = Field(gt=0)
    # Aware: naive Zeiten wären mit aware nicht vergleichbar (Sortierung wirft TypeError).
    abflug: AwareDatetime
    rueckflug: AwareDatetime | None = None
    umstiege: int = Field(default=0, ge=0)
    umstiege_rueck: int = Field(default=0, ge=0)
    airline: str = Field(default="", max_length=10)
    link: str = Field(default="", max_length=1100)


class Watch(BaseModel):
    """Eine gespeicherte Preis-Suche eines Nutzers."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[0-9a-f]{8}$")
    user_id: int
    chat_id: int
    origin: Ort
    ziel: Ort
    von: date
    bis: date
    hin_und_zurueck: bool = True
    naechte_min: int | None = Field(default=None, ge=0, le=MAX_NAECHTE)
    naechte_max: int | None = Field(default=None, ge=0, le=MAX_NAECHTE)
    max_preis: int = Field(ge=MIN_PREIS, le=MAX_PREIS)
    gemeldeter_preis: int | None = None
    letzter_preis: int | None = None
    angelegt: datetime

    @model_validator(mode="after")
    def _pruefe_spannen(self) -> "Watch":
        if self.bis < self.von:
            raise ValueError("bis liegt vor von")
        if self.origin.code == self.ziel.code:
            raise ValueError("Abflug und Ziel sind gleich")
        if self.naechte_min is not None and self.naechte_max is not None and self.naechte_min > self.naechte_max:
            raise ValueError("naechte_min größer als naechte_max")
        return self
