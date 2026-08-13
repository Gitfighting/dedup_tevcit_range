"""
OCP: materialize assembled object into s3s-fs local cache.

sky-s3 (author) fetches whole objects from s3s-fs and never reads
chunk_manifest. To keep DedupAware GET correct without editing
skyproxy.rs, assemble chunks into /tmp/s3-local-cache/{bucket}/{key}
when the cache file is missing but a manifest exists.
"""
from __future__ import annotations

import os
from typing import List, Optional


def ensure_assembled_in_cache(
    bucket: str,
    key: str,
    chunk_refs: Optional[List[dict]],
    cache_root: str = "/tmp/s3-local-cache",
    chunk_root: str = "/tmp/dedup_chunks",
    index_path: str = "/tmp/dedup_index",
    region: str = "local",
) -> bool:
    """Write assembled bytes to cache if missing. Returns True if file exists after call."""
    if not bucket or not key:
        return False
    path = os.path.join(cache_root, bucket, key)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return True
    if not chunk_refs:
        return False

    from dedup import DedupEngine  # prototype/dedup on sys.path via operations.dedup shim

    engine = DedupEngine.get_or_create(
        chunk_root=chunk_root, index_path=index_path, region=region
    )
    data = engine.assemble_from_list(chunk_refs)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".assemble.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return True
