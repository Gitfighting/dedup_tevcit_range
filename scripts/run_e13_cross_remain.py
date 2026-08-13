#!/usr/bin/env python3
"""Run remaining E13 cross-trace sims (skip T15 done, T29 OOM)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    import yaml
except ImportError:
    raise SystemExit("pip install pyyaml")


def run_sim(tag: str, cfg: str, trace: str, simv: int, vm: int) -> None:
    if list(ROOT.glob(f"simulation_results_{tag}_*.json")):
        print(f"[exists] {tag}")
        return
    args = f"--config {cfg} --trace {trace} --vm {vm} --setbase false --simversion {simv} --tag {tag}"
    env_prefix = "set MAVEN_OPTS=-Xmx8g&& " if sys.platform == "win32" else "MAVEN_OPTS=-Xmx8g "
    cmd = f'{env_prefix}mvn -q -o exec:java "-Dexec.mainClass=org.example.Main" "-Dexec.args={args}"'
    print(f"[run] {tag}")
    if subprocess.run(cmd, cwd=ROOT, shell=True).returncode != 0:
        raise RuntimeError(tag)
    milp = ROOT / "results" / "milp_solve_log.jsonl"
    arch = ROOT / "results" / f"milp_solve_log_{tag}.jsonl"
    if not arch.exists() and milp.exists():
        milp.replace(arch)


def main() -> None:
    cfg = yaml.safe_load((ROOT / "config" / "experiment_delta_saturation.yaml").read_text(encoding="utf-8"))
    vm, simv = int(cfg.get("vm", 5)), int(cfg.get("simversion", 3))
    skip_ids = {"T15", "T29"}
    for ct in cfg.get("cross_traces", []):
        if ct.get("skip") or ct["id"] in skip_ids:
            print(f"[skip] {ct['id']}")
            continue
        trace = ct["trace"].replace("\\", "/")
        tid = ct["id"]
        for policy, pcfg, psim in [
            ("tevict", cfg["tevict_config"], 1),
            ("v4_delta_ec", cfg["v4_config"], simv),
        ]:
            run_sim(f"e13_{policy}_{tid}_p100", pcfg, trace, psim, vm)


if __name__ == "__main__":
    main()
