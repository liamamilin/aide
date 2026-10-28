#!/usr/bin/env python3
"""Run the general-assistant scenario evaluation against a local Ollama (synthetic tools only)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_desktop import config  # noqa: E402
from ai_desktop.diagnostics import scenario_eval  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="+")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--num-ctx", type=int, default=8192, help="smaller than the app default, for speed")
    parser.add_argument("--output", type=Path, help="write the full JSON report here")
    args = parser.parse_args()
    chat = scenario_eval.ollama_chat(config.OLLAMA_BASE_URL)
    options = {**scenario_eval.global_options(), "num_ctx": args.num_ctx}
    reports = []
    for model in args.models:
        report = scenario_eval.evaluate(
            model, chat, repetitions=args.repetitions, options=options,
            progress=lambda s, run: print(f"[{model}] {s.id}: {'ok' if run['passed'] else 'FAIL ' + run['reason']}",
                                          flush=True))
        reports.append(report)
        print("\n" + scenario_eval.summary_table(report) + "\n", flush=True)
    if args.output:
        args.output.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
