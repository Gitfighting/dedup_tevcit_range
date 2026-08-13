"""
OCP shim for store-server imports.

Author PUT path does: ``from operations.dedup import DedupEngine``.
The DedupEngine implementation lives in ``prototype/dedup/`` (extension
module, not SkyStore VLDB artifact core). This package re-exports that
engine so we do NOT fork or rewrite author object_operations for import
paths.

Do not copy engine source here — keep a single implementation under
``prototype/dedup/``.
"""
from __future__ import annotations

import sys
from pathlib import Path

# operations/dedup/__init__.py -> operations -> store-server -> prototype
_PROTOTYPE_ROOT = Path(__file__).resolve().parents[3]
if str(_PROTOTYPE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROTOTYPE_ROOT))

from dedup import DedupEngine  # noqa: E402  (prototype/dedup)
from dedup.cache_materialize import ensure_assembled_in_cache  # noqa: E402

__all__ = ["DedupEngine", "ensure_assembled_in_cache"]
