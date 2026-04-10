"""Spazio riservato alla futura interfaccia HTTP.

Per ora espone solo un endpoint minimale di stato, così la struttura
resta pronta se in seguito vorrai riattivare FastAPI.
"""

from __future__ import annotations

from fastapi import FastAPI

from config import get_config


def build_app() -> FastAPI:
    """Costruisce l'app FastAPI minimale del progetto."""
    config = get_config()
    app = FastAPI(title="value4care-pipeline")

    @app.get("/")
    def root() -> dict[str, str]:
        return {
            "status": "ok",
            "mode": "reserved_for_future_interface",
            "ollama_model": config.ollama_model,
        }

    return app


app = build_app()
