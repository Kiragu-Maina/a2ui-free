"""The a2ui-free command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bench import ARMS, TASKS, run, summarise
from .blueprint import generate_from_plan
from .generate import generate
from .llm import LlmError
from .repair import repair
from .validate import validate


def _read(path: str) -> str:
    return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")


def _json(path: str | None):
    return None if path is None else json.loads(_read(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="a2ui-free", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    make = commands.add_parser("generate", help="make UI from a request")
    make.add_argument("request")
    make.add_argument("--model", help="for example ollama/llama3.2:3b, or a comma list")
    make.add_argument("--data", help="JSON file with the data model")
    make.add_argument("--inline", action="store_true", help="show the model the values instead of the paths")
    make.add_argument("--plan", action="store_true", help="the model writes a small plan and code builds the UI")
    make.add_argument("--attempts", type=int, default=3)

    check = commands.add_parser("check", help="validate A2UI JSON from a file or -")
    check.add_argument("file")
    check.add_argument("--data")
    check.add_argument("--no-literal-figures", action="store_true")

    fix = commands.add_parser("repair", help="repair a model reply from a file or -")
    fix.add_argument("file")

    bench = commands.add_parser("bench", help="run the experiment in docs/EXPERIMENT.md")
    bench.add_argument("--models", required=True, help="comma list")
    bench.add_argument("--trials", type=int, default=3)
    bench.add_argument("--tasks", help=f"comma list of {','.join(TASKS)}")
    bench.add_argument("--arms", help=f"comma list of {','.join(ARMS)}")
    bench.add_argument("--out", default="results.jsonl")

    report = commands.add_parser("summary", help="summarise a bench results file")
    report.add_argument("file")

    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            data = _json(args.data)
            if args.plan:
                if data is None:
                    print("--plan needs --data", file=sys.stderr)
                    return 2
                result = generate_from_plan(args.request, args.model, data, attempts=args.attempts)
            else:
                result = generate(args.request, args.model, data=data, bound=not args.inline, attempts=args.attempts)
            if not result.ok:
                print(result.error or "no valid UI", file=sys.stderr)
                print(result.report, file=sys.stderr)
                return 1
            print(json.dumps(result.messages, indent=2, ensure_ascii=False))
        elif args.command == "check":
            report_ = validate(
                json.loads(_read(args.file)),
                data=_json(args.data),
                check_bindings=args.data is not None,
                no_literal_figures=args.no_literal_figures,
            )
            print(report_)
            return 0 if report_.ok else 1
        elif args.command == "repair":
            fixed = repair(_read(args.file))
            print(json.dumps(fixed.messages, indent=2, ensure_ascii=False))
            for note in fixed.notes:
                print(f"repaired: {note}", file=sys.stderr)
        elif args.command == "bench":
            run(
                args.models.split(","),
                args.trials,
                Path(args.out),
                args.tasks.split(",") if args.tasks else None,
                args.arms.split(",") if args.arms else None,
            )
        else:
            print(json.dumps(summarise(Path(args.file)), indent=2))
    except (LlmError, OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
