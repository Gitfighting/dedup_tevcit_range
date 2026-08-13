#!/usr/bin/env python3
"""3reg FB paper matrix: 6 policies, T15/T65/T78/T79, type A-D, skip T29."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAR = ROOT / "target" / "Skystore1212-1.0-SNAPSHOT.jar"
PROFILES_SRC = ROOT / "skystore_python" / "artifact-eval-main" / "simulation" / "src" / "utils" / "profiles"
PROFILES_DST = ROOT / "target" / "profiles"

POLICIES = {
    "always_evict": "config/alwaysevict_3reg.yaml",
    "always_store": "config/alwaysstore_3reg.yaml",
    "teven": "config/teven_3reg.yaml",
    "ewma": "config/ewma_3reg.yaml",
    "tevict": "config/tevictranges_3reg.yaml",
    "optimal": "config/optimal_3reg.yaml",
}
TRACES = {"T15": "015", "T65": "065", "T78": "078", "T79": "079"}
TYPES = ["A", "B", "C", "D"]


def latest(tag: str) -> Path | None:
    m = sorted(ROOT.glob(f"simulation_results_{tag}_*.json"), key=lambda p: p.stat().st_mtime)
    return m[-1] if m else None


def main() -> int:
    import shutil
    if not JAR.is_file():
        print("missing jar", file=sys.stderr)
        return 1
    if not PROFILES_DST.exists():
        shutil.copytree(PROFILES_SRC, PROFILES_DST)

    ok = True
    for pol, cfg in POLICIES.items():
        for tid, tnum in TRACES.items():
            for wtype in TYPES:
                tag = f"fb3reg_{pol}_{tid}_type{wtype}"
                if latest(tag):
                    print(f"[skip] {tag}")
                    continue
                trace = ROOT / "Data/3reg/3reg" / f"type{wtype}" / f"IBMObjectStoreTrace{tnum}Part0.type{wtype}.mc.aug"
                if not trace.is_file():
                    print(f"[missing] {trace}")
                    ok = False
                    continue
                rel = trace.relative_to(ROOT).as_posix()
                cmd = [
                    "java", "-Xms4G", "-Xmx8G", "-jar", str(JAR),
                    "--config", cfg, "--trace", rel,
                    "--vm", "5", "--setbase", "true", "--simversion", "1", "--tag", tag,
                ]
                print(f"[run] {tag}")
                if subprocess.run(cmd, cwd=ROOT).returncode != 0:
                    print(f"[FAIL] {tag}", file=sys.stderr)
                    ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
