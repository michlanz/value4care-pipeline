"""Orchestratore locale delle fasi vaccini della pipeline di test.

Per il ramo vaccini la sequenza effettiva e:
- stage 1: parsing PDF vaccinali
- stage 3: import nel database SQLite vaccini
- stage 4: process mining sugli eventi vaccinali
- stage 5: dashboard locale

Lo stage 2 non viene usato nel ramo vaccini.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
STAGE1_PATH = ROOT_DIR / "test_stage1.py"
STAGE3_PATH = ROOT_DIR / "test_stage3.py"
STAGE4_PATH = ROOT_DIR / "test_stage4.py"
STAGE5_PATH = ROOT_DIR / "test_stage5.py"


def _build_parser() -> argparse.ArgumentParser:
    """Costruisce la CLI dell'orchestratore vaccini."""
    parser = argparse.ArgumentParser(description="Esegue in sequenza le fasi vaccini di test.")
    parser.add_argument("--person", type=str, help="Limita stage1 e stage3 a una sola persona.")
    parser.add_argument("--force", action="store_true", help="Propaga --force a stage1 e stage3.")
    parser.add_argument(
        "--debug-artifacts",
        action="store_true",
        help="Propaga --debug-artifacts a stage1.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Esegue anche stage5 oltre a stage1, stage3 e stage4.",
    )
    return parser


def _run_step(label: str, script_path: Path, extra_args: list[str]) -> int:
    """Esegue uno step della pipeline tramite subprocess."""
    command = [sys.executable, str(script_path), *extra_args]
    print(f"\n=== {label} ===", flush=True)
    print("-> Comando:", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=ROOT_DIR)
    return completed.returncode


def main() -> int:
    """Punto di ingresso dell'orchestratore vaccini."""
    args = _build_parser().parse_args()

    stage1_args: list[str] = []
    stage3_args: list[str] = []
    stage4_args: list[str] = []
    stage5_args: list[str] = []

    if args.person:
        stage1_args.extend(["--person", args.person])
        stage3_args.extend(["--person", args.person])
    if args.force:
        stage1_args.append("--force")
        stage3_args.append("--force")
    if args.debug_artifacts:
        stage1_args.append("--debug-artifacts")

    pipeline = [
        ("STAGE 1 VACCINI", STAGE1_PATH, stage1_args),
        ("STAGE 3 DATABASE VACCINI", STAGE3_PATH, stage3_args),
        ("STAGE 4 MINING VACCINI", STAGE4_PATH, stage4_args),
    ]
    if args.all:
        pipeline.append(("STAGE 5 DASHBOARD VACCINI", STAGE5_PATH, stage5_args))

    for label, script_path, extra_args in pipeline:
        exit_code = _run_step(label, script_path, extra_args)
        if exit_code != 0:
            print(f"ERRORE: {label} terminato con exit code {exit_code}.", flush=True)
            return exit_code

    print("\n=== PIPELINE VACCINI COMPLETATA ===", flush=True)
    if not args.all:
        print("-> Eseguiti stage1, stage3 e stage4.", flush=True)
    else:
        print("-> Stage5 avviato come ultima fase.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
