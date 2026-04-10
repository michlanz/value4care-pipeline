"""Filtri semplici sugli eventi clinici.

Per ora qui mettiamo utility leggere per interrogare il log o gli eventi
prima di arrivare a un vero modulo di analisi.
"""

from __future__ import annotations

from clinical import ClinicalEvent, ClinicalEventType


def filter_events_by_type(
    events: list[ClinicalEvent],
    event_type: ClinicalEventType,
) -> list[ClinicalEvent]:
    """Restituisce solo gli eventi del tipo richiesto."""
    return [event for event in events if event.event_type == event_type]
