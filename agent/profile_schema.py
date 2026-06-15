"""
Pydantic-Modelle für personal_profile.yaml – Issue #198 (Phase 226).

Weiche Validierung: Das Profil ist handbearbeitet, daher kein harter Crash
bei unbekannten Feldern. Jedes Modell erlaubt extra-Felder (extra="allow"),
sodass unbekannte Sektionen/Schlüssel erhalten bleiben statt verworfen zu werden.

Alle Kern-Felder sind optional – ein unvollständiges Profil ist gültig.
Bewusst kein strict-Mode: Pydantic v2 coerced int→str nicht automatisch,
d.h. ein echter Typfehler (z.B. name: 123) löst einen ValidationError aus,
ohne valide Coercions (z.B. "1" → str) zu verhindern.

Konsumiert wird das Profil projektweit als dict – diese Modelle dienen nur
der Validierung in profile.load_profile(), nicht als Storage-Typ.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class Identity(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    location: str | None = None
    language: str | None = None


class Work(BaseModel):
    model_config = ConfigDict(extra="allow")

    employer: str | None = None
    role: str | None = None
    focus: str | None = None
    job_context: str | None = None


class ActiveProject(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    description: str | None = None
    stack: list[str] | None = None
    priority: str | None = None


class Projects(BaseModel):
    model_config = ConfigDict(extra="allow")

    active: list[ActiveProject] | None = None


class Person(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    context: str | None = None


class Place(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    type: str | None = None
    location: str | None = None
    context: str | None = None


class Media(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str | None = None
    type: str | None = None
    artist: str | None = None
    context: str | None = None


class CustomEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str | None = None
    value: str | None = None


class PersonalProfile(BaseModel):
    """Wurzel-Modell. extra="allow" → unbekannte Top-Level-Sektionen bleiben erhalten."""

    model_config = ConfigDict(extra="allow")

    identity: Identity | None = None
    work: Work | None = None
    projects: Projects | None = None
    # people ist im realen Profil ein einzelnes dict, kann aber auch eine Liste
    # sein – beide Formen sind gültig (Union, kein Breaking Change).
    people: Person | list[Person] | None = None
    places: list[Place] | None = None
    media: list[Media] | None = None
    custom: list[CustomEntry] | None = None

    # Freie Sektionen – locker getypt, keine feste Struktur.
    preferences: dict[str, Any] | None = None
    hardware: dict[str, Any] | None = None
    routines: dict[str, Any] | None = None
    development: dict[str, Any] | None = None
    events: list[dict[str, Any]] | None = None
    archived: list[Any] | None = None
    notes: list[str] | None = None
