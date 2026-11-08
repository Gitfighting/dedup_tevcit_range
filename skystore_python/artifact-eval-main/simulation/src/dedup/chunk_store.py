"""Offline chunk-store loaders for simulation (OCP extension).

Built by scripts/cloud/chunk_objects.py + prep_chunkstore.py from
zero-error manifests + /data/file_pool. Simulation never re-runs CDC.
"""
from __future__ import annotations

import csv
import gzip
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

import numpy as np

CHUNK_FP_BYTES = 40  # control-plane manifest entry per chunk ref


@dataclass
class ChunkStore:
    """obj_key -> (uids ndarray view, logical_size_bytes)."""

    root: Path
    lens_u: np.ndarray
    objs: Dict[str, Tuple[np.ndarray, int]]
    n_unique: int

    @classmethod
    def load(cls, root: str | Path) -> "ChunkStore":
        root = Path(root)
        lens_u = np.load(root / "lens_u.npy", mmap_mode="r")
        uids_data = np.load(root / "obj_uids_data.npy", mmap_mode="r")
        indptr = np.load(root / "obj_uids_indptr.npy")
        objs: Dict[str, Tuple[np.ndarray, int]] = {}
        with gzip.open(root / "obj_meta.csv.gz", "rt") as f:
            for i, row in enumerate(csv.DictReader(f)):
                objs[row["obj_key"]] = (
                    uids_data[indptr[i] : indptr[i + 1]],
                    int(row["size"]),
                )
        return cls(
            root=root,
            lens_u=lens_u,
            objs=objs,
            n_unique=int(len(lens_u)),
        )

    def uids(self, key: str) -> np.ndarray:
        return self.objs[key][0]

    def logical_size(self, key: str) -> int:
        return self.objs[key][1]

    def unique_bytes(self, key: str) -> int:
        u = np.unique(self.uids(key))
        if len(u) == 0:
            return 0
        return int(self.lens_u[u].sum())

    def manifest_bytes(self, key: str) -> int:
        return int(len(self.uids(key)) * CHUNK_FP_BYTES)

    def has(self, key: str) -> bool:
        return key in self.objs
