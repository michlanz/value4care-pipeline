"""Concetti clinici condivisi in tutto il progetto.

Qui stanno i "mattoni di base" del dominio:
- famiglia del documento
- tipo di evento clinico
- diagnosi
- care thread

Questo file non deve conoscere né il database né Ollama né FastAPI.
Serve solo a dare un vocabolario comune al resto del codice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path


class DocumentFamily(StrEnum):
    """Classificazione preliminare dei documenti, guidata soprattutto dal filename."""

    VACCINATION_CERTIFICATE = "vaccination_certificate"
    SUMMARY = "summary"
    CLINICAL_DOCUMENT = "clinical_document"
    PRESCRIPTION = "prescription"
    UNKNOWN = "unknown"


class ClinicalEventType(StrEnum):
    """Tipi generali di eventi che possono finire nel log clinico."""

    DIAGNOSIS = "diagnosis"
    VISIT = "visit"
    EXAM = "exam"
    PRESCRIPTION = "prescription"
    VACCINATION = "vaccination"
    ACUTE_EVENT = "acute_event"
    OTHER = "other"


class CareThreadKind(StrEnum):
    """Tipi di care thread, cioè fili clinici che legano più eventi tra loro."""

    ACUTE = "acute"
    CHRONIC_FOLLOWUP = "chronic_followup"
    SCREENING_OR_INCIDENTAL = "screening_or_incidental"
    ADMINISTRATIVE = "administrative"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClinicalDocument:
    """Rappresentazione minima di un documento clinico nel sistema."""

    path: Path
    family: DocumentFamily
    patient_id: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class Diagnosis:
    """Diagnosi esplicita estratta da un documento."""

    label: str
    diagnosed_at: date | None = None
    source_document: Path | None = None


@dataclass(frozen=True)
class ClinicalEvent:
    """Evento clinico generale da usare per timeline, linking e mining."""

    event_type: ClinicalEventType
    label: str
    occurred_at: date | None = None
    source_document: Path | None = None
    diagnosis: Diagnosis | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CareThread:
    """Contenitore logico che raggruppa eventi e documenti correlati."""

    thread_id: str
    kind: CareThreadKind = CareThreadKind.UNKNOWN
    title: str | None = None
    clinical_tags: tuple[str, ...] = field(default_factory=tuple)
