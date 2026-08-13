#!/usr/bin/env python3
"""Run 2-region Docker-image cost comparison, including SkyStore+LZ4."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAR = ROOT / "target" / "Skystore1212-1.0-SNAPSHOT.jar"
TRACE = "Data/docker_image/docker_image_2reg_lz4.mc.aug"
POLICIES = {
    "always_evict": "config/alwaysevict_2reg.yaml",
    "always_store": "config/alwaysstore_2reg.yaml",
    "optimal": "config/optimal.yaml",
    "tevict_ranges": "config/tevictranges_2reg.yaml",
    "teven": "config/teven_2reg.yaml",
    "tevict_ranges_lz4": "config/tevictranges_lz4_2reg.yaml",
}


def latest(tag: str) -> Path | None:
    pattern = re.compile(rf"^simulation_results_{re.escape(tag)}_\d{{8}}_\d{{6}}\.json$")
    files = sorted(
        (p for p in ROOT.glob(f"simulation_results_{tag}_*.json") if pattern.match(p.name)),
        key=lambda p: p.stat().st_mtime,
    )
    return files[-1] if files else None


def main() -> None:
    if not JAR.is_file():
        raise SystemExit("Build the shaded JAR first: mvn -q -DskipTests package")
    for name, config in POLICIES.items():
        tag = f"docker_lz4_2reg_{name}"
        if latest(tag):
            print(f"[skip] {tag}")
            continue
        command = [
            "java", "-Xms2G", "-Xmx6G", "-jar", str(JAR),
            "--config", config, "--trace", TRACE, "--vm", "5",
            "--setbase", "true", "--simversion", "1", "--tag", tag,
        ]
        print(f"[run] {tag}")
        subprocess.run(command, cwd=ROOT, check=True)

    rows = []
    for name in POLICIES:
        result = latest(f"docker_lz4_2reg_{name}")
        if result is None:
            continue
        data = json.loads(result.read_text(encoding="utf-8"))
        rows.append({
            "policy": name,
            "total": float(data["total cost ($)"]),
            "storage": float(data["total storage cost ($)"]),
            "transfer": float(data["total transfer cost ($)"]),
            "wire_ratio": data.get("wire/logical transfer ratio", 1.0),
            "result": result.name,
        })
    rows.sort(key=lambda row: row["total"])
    output = ROOT / "results" / "docker_lz4_2reg_summary.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    import csv
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote summary: {output}")


if __name__ == "__main__":
    main()
