"""Percorsi degli artefatti prodotti dalla pipeline.

Gli artefatti sono file di supporto, per esempio:
- testo estratto dal PDF
- risposta raw del modello
"""

from __future__ import annotations

from pathlib import Path


def artifact_paths_for_document(artifacts_dir: Path, document_path: str | Path) -> dict[str, Path]:
    """Restituisce i path standard degli artefatti per un documento."""
    source_path = Path(document_path)
    stem = source_path.stem
    document_dir = artifacts_dir / stem
    return {
        "document_dir": document_dir,
        "extracted_text": document_dir / "extracted_text.txt",
        "llm_response": document_dir / "llm_response.json",
    }
