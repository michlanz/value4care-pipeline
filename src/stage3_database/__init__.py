"""Pacchetto dedicato alla persistenza e agli artefatti.

Qui non c'è ancora un database vero e proprio, ma la struttura è pronta per:
- definire le tabelle previste
- decidere dove salvare gli artefatti
"""

from .artifacts import artifact_paths_for_document
from .schema import PLANNED_TABLES, TableBlueprint

__all__ = ["PLANNED_TABLES", "TableBlueprint", "artifact_paths_for_document"]
