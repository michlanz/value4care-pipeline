"""Configurazione minima centralizzata del progetto.

Questo file raccoglie path e variabili ambiente che devono essere accessibili
da più parti del codice, senza duplicare stringhe o percorsi hardcoded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class AppConfig:
    """Oggetto immutabile con la configurazione runtime dell'applicazione."""

    project_root: Path
    src_dir: Path
    data_dir: Path
    raw_data_dir: Path
    artifacts_dir: Path
    ollama_base_url: str
    ollama_model: str


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Restituisce una sola istanza condivisa della configurazione.

    `lru_cache` evita di ricreare l'oggetto a ogni import o chiamata.
    """
    return AppConfig(
        project_root=ROOT_DIR,
        src_dir=ROOT_DIR / "src",
        data_dir=ROOT_DIR / "data",
        raw_data_dir=ROOT_DIR / "data" / "raw",
        artifacts_dir=ROOT_DIR / "artifacts",
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen3:30b-a3b"),
    )
