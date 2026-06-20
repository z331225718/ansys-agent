from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize via_copper_consistency raw CSV into a compact "
            "BRD optimization planning artifact."
        )
    )
    parser.add_argument("raw_csv", type=Path)
    parser.add_argument("--top", type=int, default=0)
    args = parser.parse_args()

    summary = summarize(args.raw_csv, top=args.top)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def summarize(raw_csv: Path, *, top: int = 0) -> dict[str, Any]:
    rows = _load_rows(raw_csv)
    pattern_map: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    route_layer_rows: dict[str, int] = defaultdict(int)
    component_rows: dict[str, int] = defaultdict(int)
    diffpairs: set[str] = set()

    for row in rows:
        route_layer = row["route_layer"]
        component_group = row["component_group"] or row["component"]
        padstack = row["padstack"]
        span = row["span"]
        key = (route_layer, component_group, padstack, span)
        item = pattern_map.setdefault(
            key,
            {
                "route_layer": route_layer,
                "component_group": component_group,
                "padstack": padstack,
                "span": span,
                "row_count": 0,
                "diffpairs": set(),
                "components": set(),
                "check_layers": set(),
            },
        )
        item["row_count"] += 1
        item["diffpairs"].add(row["diffpair"])
        item["components"].add(row["component"])
        item["check_layers"].add(row["layer"])
        route_layer_rows[route_layer] += 1
        component_rows[component_group] += 1
        if row["diffpair"]:
            diffpairs.add(row["diffpair"])

    patterns = []
    for item in pattern_map.values():
        patterns.append(
            {
                "route_layer": item["route_layer"],
                "component_group": item["component_group"],
                "padstack": item["padstack"],
                "span": item["span"],
                "row_count": item["row_count"],
                "diffpair_count": len(item["diffpairs"]),
                "component_count": len(item["components"]),
                "check_layer_count": len(item["check_layers"]),
                "check_layers": _sort_layers(item["check_layers"]),
            }
        )
    patterns.sort(
        key=lambda item: (
            -int(item["diffpair_count"]),
            _layer_sort_key(str(item["route_layer"])),
            str(item["component_group"]),
            str(item["padstack"]),
            str(item["span"]),
        )
    )
    if top > 0:
        patterns = patterns[:top]

    return {
        "source": str(raw_csv),
        "row_count": len(rows),
        "diffpair_count": len(diffpairs),
        "route_layers": [
            {"name": name, "row_count": route_layer_rows[name]}
            for name in _sort_layers(route_layer_rows)
        ],
        "component_groups": [
            {"name": name, "row_count": component_rows[name]}
            for name in sorted(component_rows)
        ],
        "pattern_count": len(pattern_map),
        "patterns": patterns,
    }


def _load_rows(raw_csv: Path) -> list[dict[str, str]]:
    with raw_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [
            {key: str(value or "") for key, value in row.items()}
            for row in reader
            if row.get("board") and not str(row["board"]).startswith("#")
        ]


def _sort_layers(values) -> list[str]:
    return sorted(values, key=_layer_sort_key)


def _layer_sort_key(layer: str) -> tuple[int, str]:
    if layer.upper() == "TOP":
        return (0, layer)
    if layer.upper() == "BOTTOM":
        return (10_000, layer)
    match = re.match(r"^L0*([0-9]+)", layer.upper())
    if match:
        return (int(match.group(1)), layer)
    return (5_000, layer)


if __name__ == "__main__":
    main()
