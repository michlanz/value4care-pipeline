"""Pacchetto dedicato alla selezione e lettura dei PDF.

Qui vive solo la parte di accesso ai documenti:
- trovare i file
- classificarli dal nome
- estrarre il testo grezzo
"""

from .classifier import classify_document_family
from .extractor import PDFTextExtraction, extract_text_from_pdf
from .selector import list_pdf_files

__all__ = [
    "PDFTextExtraction",
    "classify_document_family",
    "extract_text_from_pdf",
    "list_pdf_files",
]
