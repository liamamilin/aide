#!/usr/bin/env python3
"""Record H0 discovery or synthetic model probes without altering the app."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_desktop.llm.harness_probe import (  # noqa: E402
    DEFAULT_CANDIDATES,
    LocalOllama,
    ProbeError,
    ProbeRecorder,
    evaluate_model,
    evaluate_protocol,
    recheck_tools,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", action="append", help="Explicit local candidate; repeat to compare")
    parser.add_argument("--evaluate", action="store_true", help="Also run H0.2–H0.5 synthetic tests")
    parser.add_argument(
        "--protocol-check", action="store_true", help="Capture stream fixtures and bounded context checks"
    )
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--tool-recheck", type=Path, help="Rerun tool tasks using matching verified H0.2–H0.4 evidence")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.tool_recheck and (not args.model or len(args.model) != 1 or args.evaluate or args.protocol_check):
        parser.error("--tool-recheck requires one explicit --model and cannot combine with other evaluation modes")
    directory = args.output or ROOT / "docs" / "harness-h0" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    recorder = ProbeRecorder(LocalOllama(args.base_url), directory)
    summary = {"status": "pending", "admitted": False, "candidate_override": bool(args.model), "results": {}}
    try:
        discovery = recorder.discover(tuple(args.model or DEFAULT_CANDIDATES))
        summary["discovery"] = discovery
        for model, profile in discovery["profiles"].items():
            print(f"{model}: {profile['status']}", flush=True)
            if args.evaluate and profile["status"] == "discovered":
                print(f"{model}: starting H0.2–H0.5 synthetic probes", flush=True)
                try:
                    summary["results"][model] = evaluate_model(recorder, model, profile, args.repetitions)
                except ProbeError as exc:
                    summary["results"][model] = {
                        "admitted": False,
                        "status": "incomplete",
                        "error_code": exc.code,
                        "detail": str(exc),
                    }
                print(f"{model}: recorded {len(recorder.rows)} total requests", flush=True)
            if args.protocol_check and profile["status"] == "discovered":
                summary.setdefault("protocol", {})[model] = evaluate_protocol(recorder, model, profile)
            if args.tool_recheck and profile["status"] == "discovered":
                summary["results"][model] = recheck_tools(recorder, model, profile, args.tool_recheck)
        summary["status"] = "recorded"
        summary["admitted"] = any(result.get("admitted") is True for result in summary["results"].values())
        summary["application_tools_enabled"] = False
        return 0
    except (ProbeError, ValueError, KeyboardInterrupt) as exc:
        summary.update(
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "unavailable",
            error_code=getattr(exc, "code", "cancelled" if isinstance(exc, KeyboardInterrupt) else "invalid_baseline"),
        )
        return 2
    finally:
        (directory / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(
            f"Report: {directory / 'summary.json'}; model admitted={summary['admitted']}; app tools disabled.",
            flush=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
