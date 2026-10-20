#!/usr/bin/env python3
"""Rebuild the static site from an envelopes JSONL (the official run already writes it to out/site)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.site import build_site  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--envelopes", required=True)
    parser.add_argument("--output", default="out/site")
    parser.add_argument("--report", help="Run report JSON (for run id and completion time)")
    args = parser.parse_args()
    envelopes = [json.loads(line) for line in Path(args.envelopes).read_text(encoding="utf-8").splitlines() if line.strip()]
    report = json.loads(Path(args.report).read_text(encoding="utf-8")) if args.report else {}
    run = {"run_id": report.get("run_id") or (envelopes[0].get("run_id") if envelopes else None),
           "completed_at": report.get("completed_at") or (envelopes[0].get("completed_at") if envelopes else None)}
    manifest = build_site(envelopes, args.output, run)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
