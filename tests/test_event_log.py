"""Test minimo sulla costruzione dell'event log."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from tests import _bootstrap  # noqa: F401

from clinical import ClinicalEvent, ClinicalEventType, Diagnosis
from mining import build_event_log_rows


class EventLogTestCase(unittest.TestCase):
    """Verifica che diagnosi ed eventi entrino nel log nel modo atteso."""

    def test_diagnosis_is_preserved_as_first_level_event(self) -> None:
        diagnosis = Diagnosis(
            label="Frattura del piede",
            diagnosed_at=date(2026, 4, 10),
            source_document=Path("data/raw/person001/Documento_sanitario_demo.pdf"),
        )
        event = ClinicalEvent(
            event_type=ClinicalEventType.DIAGNOSIS,
            label=diagnosis.label,
            occurred_at=diagnosis.diagnosed_at,
            source_document=diagnosis.source_document,
            diagnosis=diagnosis,
        )

        rows = build_event_log_rows("person001", [event], care_thread_id="thread-1")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].event_type, "diagnosis")
        self.assertEqual(rows[0].timestamp, "2026-04-10")
        self.assertEqual(rows[0].care_thread_id, "thread-1")


if __name__ == "__main__":
    unittest.main()
