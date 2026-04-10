"""Selezione dei PDF presenti nel dataset."""

from __future__ import annotations

from pathlib import Path


def list_pdf_files(root: str | Path, recursive: bool = True) -> list[Path]:
    """Elenca i PDF disponibili sotto una directory."""
    base_path = Path(root)
    pattern = "**/*.pdf" if recursive else "*.pdf"
    return sorted(base_path.glob(pattern))
