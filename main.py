"""Launcher principale del progetto.

Questo file deve restare molto piccolo: serve solo ad aggiungere `src/`
al path di Python e a delegare l'esecuzione alla CLI vera e propria.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"

# Permette di importare i moduli dentro `src/` anche lanciando `python main.py`.
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from stage5_interface.cli import main as cli_main


def main() -> int:
    """Punto di ingresso unico del progetto."""
    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
