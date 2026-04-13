"""Parsing delle risposte testuali del modello.

Per ora il compito è semplice: pulire eventuali blocchi markdown ```json
e trasformare il contenuto in un oggetto Python.
"""

from __future__ import annotations

import json
from typing import Any


def parse_json_payload(raw_text: str) -> Any:
    """Converte una risposta testuale del modello in JSON parsato."""
    candidate = raw_text.strip()

    if candidate.startswith("```"):
        # Alcuni modelli avvolgono il JSON in blocchi markdown: li rimuoviamo.
        parts = candidate.split("```")
        for part in parts:
            stripped = part.strip()
            if not stripped or stripped == "json":
                continue
            if stripped.startswith("json"):
                stripped = stripped[4:].strip()
            candidate = stripped
            break

    return json.loads(candidate)
