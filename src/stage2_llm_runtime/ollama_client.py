"""Client minimale per comunicare con Ollama via HTTP.

Questo modulo non sa nulla del dominio clinico o del database:
sa solo controllare se Ollama risponde e chiedere una generazione al modello.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class OllamaHealth:
    """Esito del controllo di salute di Ollama rispetto al modello configurato."""

    ok: bool
    configured_model: str
    available_models: tuple[str, ...]


class OllamaClient:
    """Client HTTP molto semplice per il server Ollama locale."""

    def __init__(self, base_url: str, model: str, timeout_seconds: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    def health(self) -> OllamaHealth:
        """Controlla se Ollama risponde e se vede il modello configurato."""
        response = requests.get(
            f"{self.base_url}/api/tags",
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        models = tuple(model["name"] for model in payload.get("models", []))
        return OllamaHealth(
            ok=self.model in models,
            configured_model=self.model,
            available_models=models,
        )

    def generate(self, prompt: str, system: str | None = None) -> dict[str, Any]:
        """Invia un prompt a Ollama e restituisce la risposta raw in JSON."""
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system

        response = requests.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()
