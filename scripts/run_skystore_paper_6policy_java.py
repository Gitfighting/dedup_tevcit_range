#!/usr/bin/env python3
"""Run SkyStore paper 6-policy Java reproduction (3reg FP)."""

from __future__ import annotations

import json
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

TRACES = {
    "T15": ("015", "Data/3reg/3reg"),
    "T65": ("065", "Data/3reg/3reg"),
    "T78": ("078", "Data/3reg/3reg"),
    "T79": ("079", "Data/3reg/3reg"),
}
TYPES = ["A", "B", "C", "D"]


def latest_result(tag: str) -> Path | None:
    matches = sorted(ROOT.glob(f"simulation_results_{tag}_*.json"), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def ensure_profiles() -> None:
    if not PROFILES_DST.exists():
        import shutil
        shutil.copytree(PROFILES_SRC, PROFILES_DST)


def run_one(policy: str, tid: str, tnum: str, root: str, wtype: str, force: bool) -> bool:
    tag = f"fp3reg_{policy}_{tid}_type{wtype}"
    if not force and latest_result(tag):
        print(f"[skip] {tag}")
        return True
    trace = ROOT / root / f"type{wtype}" / f"IBMObjectStoreTrace{tnum}Part0.type{wtype}.mc.aug"
    if not trace.is_file():
        print(f"[missing trace] {trace}")
        return False
    rel = trace.relative_to(ROOT).as_posix()
    cmd = [
        "java", "-Xms4G", "-Xmx8G",
        "-jar", str(JAR),
        "--config", POLICIES[policy],
        "--trace", rel,
        "--vm", "5",
        "--setbase", "false",
        "--simversion", "1",
        "--tag", tag,
    ]
    print(f"[run] {tag}")
    proc = subprocess.run(cmd, cwd=ROOT)
    return proc.returncode == 0


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    p.add_argument("--policy", choices=list(POLICIES))
    p.add_argument("--trace", choices=list(TRACES))
    p.add_argument("--type", dest="wtype", choices=TYPES)
    args = p.parse_args()

    if not JAR.is_file():
        print("Build jar first: mvn -q -DskipTests package", file=sys.stderr)
        return 1
    ensure_profiles()

    policies = [args.policy] if args.policy else list(POLICIES)
    traces = {args.trace: TRACES[args.trace]} if args.trace else TRACES
    types = [args.wtype] if args.wtype else TYPES

    ok = True
    for policy in policies:
        for tid, (tnum, root) in traces.items():
            for wtype in types:
                if not run_one(policy, tid, tnum, root, wtype, args.force):
                    ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
