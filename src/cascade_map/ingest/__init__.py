"""Card 1 -- ingestion and inventory.

Walks a target tree, parses each Python file with `ast`, mints stable IDs
(`module::qualname[#n]`), and emits `Element`/`Unresolved` records. Never
imports, execs, evals or unpickles anything under the walked root.
"""

from __future__ import annotations

from .inventory import Ingestor, inventory

__all__ = ["Ingestor", "inventory"]
