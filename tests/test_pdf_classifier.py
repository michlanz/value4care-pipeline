"""Test minimo sulla classificazione dei filename PDF."""

from __future__ import annotations

import unittest
from pathlib import Path

from tests import _bootstrap  # noqa: F401

from clinical import DocumentFamily
from pdf_reading import classify_document_family


class DocumentClassifierTestCase(unittest.TestCase):
    """Verifica che i prefissi noti vengano riconosciuti correttamente."""

    def test_known_prefixes_are_classified(self) -> None:
        self.assertEqual(
            classify_document_family(
                Path("CertificatoVaccinale_LMNLCU02E15D918M_20260303104746.pdf")
            ),
            DocumentFamily.VACCINATION_CERTIFICATE,
        )
        self.assertEqual(
            classify_document_family(
                Path("Riepilogo_FSE_LMNLCU02E15D918M_20260303_2250.pdf")
            ),
            DocumentFamily.SUMMARY,
        )
        self.assertEqual(
            classify_document_family(
                Path("Documento_sanitario_LMNLCU02E15D918M_20260303224554.pdf")
            ),
            DocumentFamily.CLINICAL_DOCUMENT,
        )
        self.assertEqual(
            classify_document_family(Path("Ricetta_030A04676733262_20260303_104449.pdf")),
            DocumentFamily.PRESCRIPTION,
        )

    def test_unknown_prefix_falls_back_to_unknown(self) -> None:
        self.assertEqual(
            classify_document_family(Path("QualcosaDiNuovo.pdf")),
            DocumentFamily.UNKNOWN,
        )


if __name__ == "__main__":
    unittest.main()
