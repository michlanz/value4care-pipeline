"""Estrazione del testo grezzo dai PDF.

Questo modulo usa `pdfplumber` per leggere il testo nativo del PDF.
Non interpreta il contenuto clinico: si limita a tirare fuori il testo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pdfplumber


@dataclass(frozen=True)
class PDFTextExtraction:
    """Risultato della lettura di un PDF."""

    source_path: Path
    page_count: int
    text: str

    @property
    def character_count(self) -> int:
        """Numero di caratteri del testo estratto, utile per controlli rapidi."""
        return len(self.text)


def extract_text_from_pdf(path: str | Path) -> PDFTextExtraction:
    """Apre un PDF e ne restituisce il testo concatenato pagina per pagina."""
    source_path = Path(path)
    page_text: list[str] = []

    with pdfplumber.open(source_path) as pdf:
        for page in pdf.pages:
            # Se una pagina non contiene testo estraibile, la lasciamo vuota.
            page_text.append(page.extract_text() or "")
        return PDFTextExtraction(
            source_path=source_path,
            page_count=len(pdf.pages),
            text="\n\n".join(page_text).strip(),
        )
