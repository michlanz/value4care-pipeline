"""Costruzione dei prompt per il modello.

Qui la logica deve restare leggibile e separata dal client HTTP:
il client manda il prompt, questo modulo decide come scriverlo.
"""

from __future__ import annotations

from clinical import DocumentFamily


def build_document_prompt(document_family: DocumentFamily, extracted_text: str) -> str:
    """Costruisce un prompt base per l'estrazione strutturata del documento."""
    return (
        "You are extracting structured clinical information from an offline medical record.\n"
        f"Document family: {document_family.value}\n"
        "Return only JSON.\n"
        "Preserve diagnoses as first-level events when they are explicitly present.\n\n"
        "Document text:\n"
        f"{extracted_text}"
    )
