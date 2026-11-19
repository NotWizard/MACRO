#!/usr/bin/env python3
"""Collect official data / validate Agent evidence without requiring a running server."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.core import compute_analysis, compute_collect, compute_service, compute_store
from backend.app.schemas.compute import ObservationBatch, AgentReport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect")
    collect.add_argument("--sources", nargs="+", choices=["sec", "tw", "gpu", "rental_history"], default=["sec", "tw", "gpu", "rental_history"])
    collect.add_argument("--months", type=int, default=36)
    collect.add_argument("--force", action="store_true")
    imp = commands.add_parser("import")
    imp.add_argument("--file", type=Path, required=True)
    imp.add_argument("--apply", action="store_true", help="default is validation only")
    commands.add_parser("contract")
    commands.add_parser("overview")
    report = commands.add_parser("report", help="validate/publish conclusions written by the local Agent")
    report.add_argument("--file", type=Path, required=True)
    report.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command == "collect":
        if not 0 <= args.months <= 120:
            parser.error("months must be 0–120")
        result = compute_collect.collect_all(args.sources, args.months, args.force,
            lambda frac: print(f"progress {frac:.0%}", file=sys.stderr, flush=True))
    elif args.command == "import":
        batch = ObservationBatch.model_validate_json(args.file.read_text())
        result = compute_store.import_batch(batch, dry_run=not args.apply)
        if args.apply:
            compute_store.log("agent", "ok", result["inserted"], f"CLI 导入 {len(batch.observations)} 项")
    elif args.command == "contract":
        result = compute_service.contract()
    elif args.command == "report":
        result = compute_service.publish(AgentReport.model_validate_json(args.file.read_text()), dry_run=not args.apply)
    else:
        result = compute_analysis.overview()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("status") in {"error", "busy"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
