#!/usr/bin/env python3
"""Generate a deterministic 2-region FB workload from the docker_image profile."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "results" / "docker_image_lz4_profile.csv"
OUTPUT = ROOT / "Data" / "docker_image" / "docker_image_2reg_lz4.mc.aug"
WEST = "aws:us-west-2"
EAST = "aws:us-east-1"
HOUR_MS = 3_600_000


def main() -> None:
    if not PROFILE.is_file():
        raise SystemExit(f"Missing profile: {PROFILE}")
    with PROFILE.open(newline="", encoding="utf-8") as f:
        objects = list(csv.DictReader(f))
    if len(objects) != 20:
        raise SystemExit(f"Expected 20 docker images, got {len(objects)}")

    events: list[dict[str, object]] = []
    for i, obj in enumerate(objects):
        key = obj["obj_key"]
        size = int(obj["logical_size"])
        compressed = int(obj["compressed_size"])
        events.append(dict(timestamp=i * 1000, op="PUT", issue_region=WEST,
                           obj_key=key, size=size, compressed_size=compressed))

        if i < 5:  # hot: hourly for seven days
            hours = range(1, 169)
        elif i < 10:  # warm: every six hours
            hours = range(6, 169, 6)
        elif i < 15:  # bursty: first day, then one tail read
            hours = list(range(1, 25)) + [168]
        else:  # cold/one-hit
            hours = [24]
        for hour in hours:
            events.append(dict(timestamp=hour * HOUR_MS + i * 1000, op="GET",
                               issue_region=EAST, obj_key=key, size=size,
                               compressed_size=compressed))

    events.sort(key=lambda e: (int(e["timestamp"]), str(e["obj_key"])))
    by_obj: dict[str, list[int]] = defaultdict(list)
    by_obj_region: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, event in enumerate(events):
        by_obj[str(event["obj_key"])].append(index)
        by_obj_region[(str(event["obj_key"]), str(event["issue_region"]))].append(index)

    next_any = [-1] * len(events)
    next_same = [-1] * len(events)
    for indices in by_obj.values():
        for current, nxt in zip(indices, indices[1:]):
            next_any[current] = int(events[nxt]["timestamp"])
    for indices in by_obj_region.values():
        for current, nxt in zip(indices, indices[1:]):
            next_same[current] = int(events[nxt]["timestamp"])

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fields = ["timestamp", "op", "issue_region", "obj_key", "size",
              "compressed_size", "time_to_next_access", "time_to_next_access_same_reg"]
    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for i, event in enumerate(events):
            writer.writerow({**event, "time_to_next_access": next_any[i],
                             "time_to_next_access_same_reg": next_same[i]})
    print(f"Wrote {len(events)} requests to {OUTPUT}")


if __name__ == "__main__":
    main()
