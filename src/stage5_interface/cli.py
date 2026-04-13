"""Interfaccia da terminale del progetto.

Questa è la modalità d'uso principale per la fase iniziale:
da qui puoi controllare configurazione, classificazione PDF e lettura testo.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from clinical import DocumentFamily
from config import get_config
from llm_runtime import OllamaClient
from pdf_reading import classify_document_family, extract_text_from_pdf, list_pdf_files


def _print_pdf_paths(root: Path, family: DocumentFamily | None = None) -> None:
    """Stampa i PDF trovati, con eventuale filtro per famiglia documento."""
    paths = list_pdf_files(root)

    if family is not None:
        paths = [
            path for path in paths if classify_document_family(path) == family
        ]

    for path in paths:
        print(path)


def _print_text_preview(path: Path, preview_chars: int) -> None:
    """Stampa un'anteprima del testo estratto da un PDF."""
    extraction = extract_text_from_pdf(path)
    preview = extraction.text[:preview_chars]
    print(
        json.dumps(
            {
                "source_path": str(extraction.source_path),
                "page_count": extraction.page_count,
                "character_count": extraction.character_count,
                "preview": preview,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    """Definisce i comandi disponibili da terminale."""
    parser = argparse.ArgumentParser(
        prog="value4care",
        description="CLI locale per esplorare la pipeline offline.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health", help="Mostra la configurazione attiva del progetto.")
    subparsers.add_parser(
        "ollama-health",
        help="Controlla se il modello configurato e visibile a Ollama.",
    )

    list_pdfs_parser = subparsers.add_parser(
        "list-pdfs",
        help="Elenca i PDF disponibili sotto una directory.",
    )
    list_pdfs_parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory da scandire. Default: data/raw.",
    )
    list_pdfs_parser.add_argument(
        "--family",
        choices=[family.value for family in DocumentFamily],
        default=None,
        help="Filtra i file per famiglia documento.",
    )

    classify_parser = subparsers.add_parser(
        "classify",
        help="Classifica un documento a partire dal filename.",
    )
    classify_parser.add_argument("path", type=Path, help="Percorso del PDF.")

    extract_parser = subparsers.add_parser(
        "extract-text",
        help="Estrae testo da un PDF e mostra un'anteprima.",
    )
    extract_parser.add_argument("path", type=Path, help="Percorso del PDF.")
    extract_parser.add_argument(
        "--preview-chars",
        type=int,
        default=500,
        help="Numero massimo di caratteri da mostrare.",
    )

    vaccini_parser = subparsers.add_parser(
        "vaccini",
        help="Lavora solo sui documenti vaccinali.",
    )
    vaccini_subparsers = vaccini_parser.add_subparsers(
        dest="vaccini_command",
        required=True,
    )

    vaccini_list_parser = vaccini_subparsers.add_parser(
        "list",
        help="Elenca solo i PDF di vaccinazione.",
    )
    vaccini_list_parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory da scandire. Default: data/raw.",
    )

    vaccini_extract_parser = vaccini_subparsers.add_parser(
        "extract-text",
        help="Estrae testo da un PDF vaccinale.",
    )
    vaccini_extract_parser.add_argument("path", type=Path, help="Percorso del PDF.")
    vaccini_extract_parser.add_argument(
        "--preview-chars",
        type=int,
        default=500,
        help="Numero massimo di caratteri da mostrare.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Esegue il comando richiesto da terminale."""
    parser = build_parser()
    args = parser.parse_args(argv)
    config = get_config()

    if args.command == "health":
        # Stampa la configurazione attiva, utile per capire dove sta guardando il progetto.
        print(
            json.dumps(
                {
                    "project_root": str(config.project_root),
                    "data_dir": str(config.data_dir),
                    "raw_data_dir": str(config.raw_data_dir),
                    "artifacts_dir": str(config.artifacts_dir),
                    "ollama_base_url": config.ollama_base_url,
                    "ollama_model": config.ollama_model,
                },
                indent=2,
            )
        )
        return 0

    if args.command == "ollama-health":
        # Verifica se il server Ollama risponde e se il modello configurato è disponibile.
        client = OllamaClient(
            base_url=config.ollama_base_url,
            model=config.ollama_model,
        )
        health = client.health()
        print(
            json.dumps(
                {
                    "ok": health.ok,
                    "configured_model": health.configured_model,
                    "available_models": list(health.available_models),
                },
                indent=2,
            )
        )
        return 0

    if args.command == "list-pdfs":
        # Elenca i PDF trovati nella cartella dati.
        root = args.root or config.raw_data_dir
        selected_family = DocumentFamily(args.family) if args.family is not None else None
        _print_pdf_paths(root, selected_family)
        return 0

    if args.command == "classify":
        # Usa solo il filename per assegnare una famiglia iniziale al documento.
        family = classify_document_family(args.path)
        print(family.value)
        return 0

    if args.command == "extract-text":
        # Estrae il testo grezzo dal PDF e mostra solo un'anteprima a terminale.
        _print_text_preview(args.path, args.preview_chars)
        return 0

    if args.command == "vaccini":
        # Scorciatoia esplicita per lavorare solo sul sottoinsieme vaccinale.
        if args.vaccini_command == "list":
            root = args.root or config.raw_data_dir
            _print_pdf_paths(root, DocumentFamily.VACCINATION_CERTIFICATE)
            return 0

        if args.vaccini_command == "extract-text":
            _print_text_preview(args.path, args.preview_chars)
            return 0

        parser.error(f"Unsupported vaccini command: {args.vaccini_command}")
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2
