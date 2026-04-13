"""Classificazione preliminare dei documenti a partire dal filename.

Questa è una classificazione "cheap" ma molto utile:
non chiede nulla all'LLM e sfrutta le convenzioni dei nomi file che conosci già.
"""

from __future__ import annotations

from pathlib import Path

from clinical import DocumentFamily


FILENAME_PREFIXES: tuple[tuple[str, DocumentFamily], ...] = (
    ("CertificatoVaccinale_", DocumentFamily.VACCINATION_CERTIFICATE),
    ("Riepilogo_", DocumentFamily.SUMMARY),
    ("Documento_sanitario_", DocumentFamily.CLINICAL_DOCUMENT),
    ("Ricetta_", DocumentFamily.PRESCRIPTION),
)


def classify_document_family(path: str | Path) -> DocumentFamily:
    """Restituisce la famiglia documento usando il prefisso del file."""
    filename = Path(path).name
    for prefix, family in FILENAME_PREFIXES:
        if filename.startswith(prefix):
            return family
    return DocumentFamily.UNKNOWN
