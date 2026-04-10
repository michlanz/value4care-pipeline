"""Costruzione dell'event log a partire dagli eventi clinici.

Questo modulo prende eventi già strutturati e li trasforma in righe
più semplici da usare per timeline, filtri e process mining.
"""

from __future__ import annotations

from dataclasses import dataclass

from clinical import ClinicalEvent


@dataclass(frozen=True)
class EventLogRow:
    """Riga semplificata di event log."""

    patient_id: str
    activity: str
    event_type: str
    timestamp: str | None
    source_document: str | None
    care_thread_id: str | None = None


def build_event_log_rows(
    patient_id: str,
    events: list[ClinicalEvent],
    care_thread_id: str | None = None,
) -> list[EventLogRow]:
    """Converte una lista di eventi clinici in righe di event log."""
    rows: list[EventLogRow] = []
    for event in events:
        rows.append(
            EventLogRow(
                patient_id=patient_id,
                activity=event.label,
                event_type=event.event_type.value,
                timestamp=event.occurred_at.isoformat() if event.occurred_at else None,
                source_document=str(event.source_document) if event.source_document else None,
                care_thread_id=care_thread_id,
            )
        )
    return rows
