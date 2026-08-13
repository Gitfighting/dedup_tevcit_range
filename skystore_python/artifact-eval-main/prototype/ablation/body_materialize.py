#!/usr/bin/env python3
"""
Materialize exact object payloads from zero-error manifests + /data/file_pool.

Online ablation only — ports the offline chunk_objects.payload_buffers recipe
without touching author store-server / s3-proxy.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import os
from pathlib import Path
from typing import Dict, Iterator, Optional

BUF = 32 * 1024 * 1024
DEFAULT_POOL = Path("/data/file_pool")
DEFAULT_MAN = Path("/data/manifests")


def payload_buffers(row: dict, pool: Path) -> Iterator[bytes]:
    """Yield the object's payload as a sequence of byte buffers."""
    action = row["action"]
    size = int(row["size_trace"])
    if action == "empty" or size == 0:
        return
    path = pool / row["file_rel"]
    fsize = int(row["file_size"])
    if action == "extent":
        start, remaining = int(row["offset"]), size
    elif action in ("trim", "exact"):
        start, remaining = 0, size
    elif action == "pad":
        start, remaining = 0, fsize
    else:
        raise ValueError(f"unknown action {action}")

    with open(path, "rb") as f:
        f.seek(start)
        while remaining > 0:
            buf = f.read(min(BUF, remaining))
            if not buf:
                raise IOError(f"short read {path} at {f.tell()}")
            remaining -= len(buf)
            yield buf
        if action == "pad":
            need = size - fsize
            while need > 0:
                f.seek(0)
                take = min(fsize, need)
                got = 0
                while got < take:
                    buf = f.read(min(BUF, take - got))
                    got += len(buf)
                    yield buf
                need -= take


def safe_name(obj_key: str) -> str:
    """Filesystem-safe cache name; keep readable prefix when possible."""
    cleaned = obj_key.replace("/", "__").replace("\\", "__")
    if len(cleaned) > 180:
        h = hashlib.sha1(obj_key.encode()).hexdigest()[:16]
        cleaned = cleaned[:120] + "_" + h
    return cleaned


def load_manifest(manifest: Path) -> Dict[str, dict]:
    open_fn = gzip.open if str(manifest).endswith(".gz") else open
    with open_fn(manifest, "rt", newline="") as f:
        rows = list(csv.DictReader(f))
    return {r["obj_key"]: r for r in rows}


def materialize_object(
    obj_key: str,
    size: int,
    body_dir: Path,
    recipes: Dict[str, dict],
    pool: Path,
    force: bool = False,
) -> Path:
    """
    Write exact `size` bytes for obj_key into body_dir/{safe}.bin.
    Prefer manifest recipe; if missing, raise (do not silently zero-fill).
    """
    body_dir.mkdir(parents=True, exist_ok=True)
    out = body_dir / f"{safe_name(obj_key)}.bin"
    meta = body_dir / f"{safe_name(obj_key)}.key"
    if out.is_file() and not force and out.stat().st_size == size:
        return out
    row = recipes.get(obj_key)
    if row is None:
        raise KeyError(f"obj_key not in manifest: {obj_key!r}")
    want = int(row["size_trace"])
    if want != size:
        # Trace size must match recipe; allow caller size if equal after int()
        raise ValueError(
            f"size mismatch for {obj_key}: trace={size} manifest={want}"
        )
    written = 0
    with open(out, "wb") as dest:
        for buf in payload_buffers(row, pool):
            dest.write(buf)
            written += len(buf)
    if written != size:
        raise IOError(f"wrote {written} != {size} for {obj_key}")
    meta.write_text(obj_key, encoding="utf-8")
    return out


def resolve_manifest(tag: str, man_dir: Path = DEFAULT_MAN) -> Path:
    """T065 -> IBMObjectStoreTrace065Part0.typeE.mc-aug.manifest.csv.gz"""
    num = tag[1:] if tag.upper().startswith("T") else tag
    name = f"IBMObjectStoreTrace{num}Part0.typeE.mc-aug.manifest.csv.gz"
    p = man_dir / name
    if not p.is_file():
        raise FileNotFoundError(p)
    return p


def preload(
    recipes: Dict[str, dict],
    body_dir: Path,
    pool: Path,
    keys: Optional[set] = None,
) -> int:
    """Materialize a subset (or all) objects; return count written/verified."""
    n = 0
    for key, row in recipes.items():
        if keys is not None and key not in keys:
            continue
        materialize_object(key, int(row["size_trace"]), body_dir, recipes, pool)
        n += 1
        if n % 200 == 0:
            print(f"[body] materialized {n}", flush=True)
    return n


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="T065")
    ap.add_argument("--manifest", default="")
    ap.add_argument("--pool", default=str(DEFAULT_POOL))
    ap.add_argument("--body-dir", default="/data/ablation_exp/bodies/T065")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    man = Path(args.manifest) if args.manifest else resolve_manifest(args.tag)
    recipes = load_manifest(man)
    if args.limit:
        keys = set(list(recipes.keys())[: args.limit])
    else:
        keys = None
    print({"manifest": str(man), "objects": len(recipes)})
    n = preload(recipes, Path(args.body_dir), Path(args.pool), keys)
    print({"materialized": n, "body_dir": args.body_dir})
