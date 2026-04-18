#!/usr/bin/env python3
"""Compare two Cobertura-format ``coverage.xml`` files and fail on regression.

Used by ``.github/workflows/test.yml`` to gate PRs on coverage. Run as:

    python scripts/compare_coverage.py BASE.xml PR.xml [--max-drop 1.0]

Exit code is 0 when coverage held or improved (within the ``--max-drop``
tolerance), 1 when it regressed, 2 when inputs couldn't be parsed. The
comparison uses Cobertura's ``line-rate`` attribute on the root element
(matches what ``coverage.py`` writes).
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _read_line_rate(path: Path) -> float:
    """Return the ``line-rate`` percentage (0-100) from a coverage.xml file."""
    tree = ET.parse(path)
    root = tree.getroot()
    rate = root.get("line-rate")
    if rate is None:
        print(f"error: no line-rate attribute in {path}", file=sys.stderr)
        sys.exit(2)
    return float(rate) * 100.0


def main() -> int:
    """Parse CLI args and run the comparison, returning the exit code."""
    parser = argparse.ArgumentParser(
        description="Fail on coverage regressions between two coverage.xml files.",
    )
    parser.add_argument("base", type=Path, help="Baseline coverage.xml (e.g. PR base)")
    parser.add_argument("pr", type=Path, help="Candidate coverage.xml (e.g. PR head)")
    parser.add_argument(
        "--max-drop",
        type=float,
        default=1.0,
        help="Maximum allowed coverage decrease in percentage points (default: 1.0)",
    )
    args = parser.parse_args()

    if not args.base.is_file():
        print(f"error: {args.base} does not exist", file=sys.stderr)
        return 2
    if not args.pr.is_file():
        print(f"error: {args.pr} does not exist", file=sys.stderr)
        return 2

    base_pct = _read_line_rate(args.base)
    pr_pct = _read_line_rate(args.pr)
    delta = pr_pct - base_pct

    arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "=")
    print(f"Base:  {base_pct:5.2f}%")
    print(f"PR:    {pr_pct:5.2f}%")
    print(f"Delta: {delta:+.2f} pts {arrow}")

    if delta < -args.max_drop:
        print(
            f"\nFAIL: coverage dropped by {-delta:.2f} pts (max allowed {args.max_drop:.2f}).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
