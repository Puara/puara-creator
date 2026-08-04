#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""Assert that a scoring run cleared a floor, for use in continuous integration.

The floor is deliberately low. This guards against the loop silently producing nothing —
an empty corpus, a descriptor that never fires, a clock mismatch — rather than against a
descriptor being mediocre, which is a judgement for a human reading the report.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="results.json written by `score --json`.")
    parser.add_argument("--min-recall", type=float, default=0.5)
    parser.add_argument("--max-fp-per-min", type=float, default=float("inf"))
    args = parser.parse_args()

    data = json.loads(args.results.read_text())
    overall = data["overall"]
    recall = float(overall["recall"])
    fp = float(overall["fp_per_min_ambient"])
    print(
        f"recall {recall:.3f}  fp/min {fp:.2f}  "
        f"references {overall['references']}  latency p50 {overall['latency_p50_ms']} ms"
    )

    problems = []
    if overall["references"] == 0:
        problems.append("no reference instances: the corpus has no labels to score against")
    if recall < args.min_recall:
        problems.append(f"recall {recall:.3f} is below the floor of {args.min_recall}")
    if fp > args.max_fp_per_min:
        problems.append(f"fp/min {fp:.2f} is above the ceiling of {args.max_fp_per_min}")

    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
