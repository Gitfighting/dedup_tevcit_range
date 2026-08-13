#!/usr/bin/env python3
"""
Bridge author hardcoded relative trace paths without editing author policies.

SkyStore (author) reads:
  ../experiment/trace/Tr065.typeE.3reg.tevict.prototype
AlwaysStore (author) reads:
  ../experiment/trace/test.csv

DedupAwareTevict (extension) already honors TRACE_FILE_PATH.

Call from store-server cwd (or pass --store-server-dir) before starting
the server so author code keeps working unchanged (OCP).
"""
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


AUTHOR_SKYSTORE_REL = Path("../experiment/trace/Tr065.typeE.3reg.tevict.prototype")
AUTHOR_ALWAYS_STORE_REL = Path("../experiment/trace/test.csv")


def prepare_trace_bridge(store_server_dir: Path, real_trace: Path) -> dict:
    """Create experiment/trace files expected by author policies."""
    store_server_dir = store_server_dir.resolve()
    real_trace = real_trace.resolve()
    if not real_trace.is_file():
        raise FileNotFoundError(real_trace)

    exp_trace = (store_server_dir / ".." / "experiment" / "trace").resolve()
    exp_trace.mkdir(parents=True, exist_ok=True)

    targets = {
        "skystore": exp_trace / AUTHOR_SKYSTORE_REL.name,
        "always_store": exp_trace / "test.csv",
    }
    for label, dest in targets.items():
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        try:
            os.symlink(real_trace, dest)
            mode = "symlink"
        except OSError:
            shutil.copy2(real_trace, dest)
            mode = "copy"
        print(f"[trace_bridge] {label}: {dest} <- {real_trace} ({mode})")

    # Also export for DedupAwareTevict
    os.environ["TRACE_FILE_PATH"] = str(real_trace)
    print(f"[trace_bridge] TRACE_FILE_PATH={real_trace}")
    return {"TRACE_FILE_PATH": str(real_trace), **{k: str(v) for k, v in targets.items()}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store-server-dir", required=True)
    ap.add_argument("--trace", required=True, help="Absolute path to .mc-aug or .prototype")
    args = ap.parse_args()
    prepare_trace_bridge(Path(args.store_server_dir), Path(args.trace))


if __name__ == "__main__":
    main()
