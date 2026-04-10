"""Bootstrap minimale per i test.

Aggiunge `src/` al path così i test possono importare i moduli del progetto
senza richiedere un packaging Python più avanzato in questa fase.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
