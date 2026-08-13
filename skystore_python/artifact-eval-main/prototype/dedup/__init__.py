"""
DedupEngine — traditional content-defined deduplication layer.

Components:
    cdc_chunker  — FastCDC content-defined chunking (USENIX ATC 2016)
    dedup_index  — two-stage index (Bloom filter + LMDB)
    dedup_store  — chunk physical storage
    dedup_engine — unified chunk/dedup/assemble interface
"""

from .dedup_engine import DedupEngine
from .cache_materialize import ensure_assembled_in_cache

__all__ = ["DedupEngine", "ensure_assembled_in_cache"]
