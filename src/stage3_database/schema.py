"""Schema logico iniziale della persistenza.

Qui non stiamo ancora creando tabelle reali nel database:
stiamo solo dichiarando quali tabelle prevediamo di avere e a cosa servono.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TableBlueprint:
    """Descrizione minima di una tabella prevista."""

    name: str
    purpose: str


PLANNED_TABLES: tuple[TableBlueprint, ...] = (
    TableBlueprint("patients", "Anagrafica e riferimenti del paziente."),
    TableBlueprint("documents", "Metadati e classificazione dei documenti sorgente."),
    TableBlueprint("document_artifacts", "Testo estratto e output raw del modello."),
    TableBlueprint("clinical_events", "Eventi clinici generali per timeline e mining."),
    TableBlueprint("document_links", "Collegamenti espliciti tra documenti."),
    TableBlueprint("care_threads", "Fili clinici che raggruppano eventi correlati."),
    TableBlueprint("thread_memberships", "Relazione tra documenti, eventi e care thread."),
    TableBlueprint("vaccinations", "Verticale dedicata alla casistica vaccinale."),
)
