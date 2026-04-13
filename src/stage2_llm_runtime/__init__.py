"""Pacchetto dedicato al runtime del modello LLM.

Qui stanno:
- il client per parlare con Ollama
- i prompt
- il parsing delle risposte
"""

from .ollama_client import OllamaClient, OllamaHealth
from .parsing import parse_json_payload
from .prompts import build_document_prompt

__all__ = [
    "OllamaClient",
    "OllamaHealth",
    "build_document_prompt",
    "parse_json_payload",
]
