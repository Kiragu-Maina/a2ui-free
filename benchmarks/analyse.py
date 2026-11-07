"""Recomputes every number in docs/EXPERIMENT.md from the stored replies.

Run from the repository root: python benchmarks/analyse.py
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from pathlib import Path

from a2ui_free.bench import TASKS, explain_figure
from a2ui_free.blueprint import read_plan
from a2ui_free.repair import extract_json

HERE = Path(__file__).parent

# One reader's judgement, made by reading all 207 label and path pairs. A label
# is listed when it says something untrue about its value. Vague labels such as
# "Amount" on /owed are not listed.
FALSE_LABELS = {
    ("account", "Current Balance"),  # bound to the holder's name
    ("invoice", "Acme Corp"),  # an invented company beside the real customer
    ("weather", "Sunday"),  # bound to the city
    ("weather", "Sunny"),  # bound to the city, and a claim about the weather
    ("weather", "Tomorrow"),  # bound to the city
}
PLACEHOLDER_LABELS = {("invoice", "Words")}  # copied from the prompt's example


def read(name: str) -> list[dict]:
    return [json.loads(line) for line in (HERE / name).read_text().splitlines() if line.strip()]


def wilson(hits: int, n: int, z: float = 1.96) -> str:
    p = hits / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return f"{100 * (centre - half):.0f} to {100 * (centre + half):.0f}%"


def valid(run: dict) -> bool:
    return run["final_valid"]


def useful(run: dict) -> bool:
    return run["final_valid"] and run["coverage"] >= 0.5


def sign_test(wins: int, losses: int) -> float:
    n = wins + losses
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(max(wins, losses), n + 1)) / 2**n) if n else 1.0


def labels(run: dict) -> list[tuple[str, str]]:
    plan, _ = read_plan(extract_json(run["replies"][-1]), TASKS[run["task"]][1])
    found: list[tuple[str, str]] = []
    for block in plan.blocks:
        found += [(run["task"], item["label"]) for item in block.get("fields", [])]
        found += [(run["task"], column["label"]) for column in block.get("table", {}).get("columns", [])]
    return found


def main() -> None:
    main_run = read("results-2026-11-06.jsonl")
    fixed = read("blueprint-fixed-2026-11-07.jsonl")
    arms = {
        "inline": [r for r in main_run if r["arm"] == "inline"],
        "bound": [r for r in main_run if r["arm"] == "bound"],
        "blueprint before the fix": [r for r in main_run if r["arm"] == "blueprint"],
        "blueprint": fixed,
    }
    for name, runs in arms.items():
        n = len(runs)
        passed = [r for r in runs if r["final_valid"]]
        print(f"\n{name} ({n} runs)")
        print(f"  valid first try  {sum(r['raw_valid'] for r in runs)}")
        print(f"  valid at the end {len(passed)}  ({wilson(len(passed), n)})")
        print(f"  useful           {sum(map(useful, runs))}  ({wilson(sum(map(useful, runs)), n)})")
        print(f"  valid and blank  {sum(r['coverage'] == 0 for r in passed)}")
        print(f"  not in source    {sum(r['figures_wrong'] for r in runs)} of {sum(r['figures_shown'] for r in runs)}")
        print(f"    on valid runs  {sum(r['figures_wrong'] for r in passed)} of {sum(r['figures_shown'] for r in passed)}")
        print(f"  median seconds   {statistics.median(r['seconds'] for r in runs)}")

    print("\nBy model: valid and useful")
    for model in sorted({r["model"] for r in main_run}):
        print(" ", model, {a: (sum(valid(r) for r in arms[a] if r["model"] == model),
                               sum(useful(r) for r in arms[a] if r["model"] == model))
                           for a in ("inline", "bound", "blueprint")})
    print("\nBy task: valid and useful")
    for task in TASKS:
        print(" ", task, {a: (sum(valid(r) for r in arms[a] if r["task"] == task),
                              sum(useful(r) for r in arms[a] if r["task"] == task))
                          for a in ("inline", "bound", "blueprint")})

    key = lambda r: (r["model"], r["task"], r["trial"])
    blueprint = {key(r): r for r in fixed}
    print("\nPaired, blueprint against each arm (see the caveat in EXPERIMENT.md)")
    for name in ("inline", "bound"):
        other = {key(r): r for r in arms[name]}
        for label, test in (("valid", valid), ("useful", useful)):
            wins = sum(test(blueprint[k]) and not test(other[k]) for k in blueprint)
            losses = sum(test(other[k]) and not test(blueprint[k]) for k in blueprint)
            print(f"  {name} {label}: blueprint alone {wins}, {name} alone {losses}, sign test p={sign_test(wins, losses):.2g}")

    print("\nFigures not in the source")
    for run in main_run:
        for figure in run["wrong_figures"]:
            print(f"  {run['model']} {run['task']} trial {run['trial']} valid={run['final_valid']}: "
                  f"{figure} {explain_figure(figure, TASKS[run['task']][1])}")

    passed = [r for r in fixed if r["final_valid"]]
    every = [label for run in passed for label in labels(run)]
    false = [label for label in every if label in FALSE_LABELS]
    print("\nLabel audit of valid blueprint runs")
    print(f"  labels {len(every)}, false {len(false)}, placeholder {sum(l in PLACEHOLDER_LABELS for l in every)}")
    print(f"  runs with a false label {sum(any(l in FALSE_LABELS for l in labels(r)) for r in passed)} of {len(passed)}")
    print(" ", Counter(false))

    print("\nBlocks holding more than one part, in the first blueprint replies")
    total = mixed = 0
    for run in arms["blueprint before the fix"]:
        for reply in run["replies"]:
            value = extract_json(reply)
            if isinstance(value, dict) and isinstance(value.get("blocks"), list):
                for block in value["blocks"]:
                    if isinstance(block, dict):
                        total += 1
                        mixed += len({"heading", "fields", "table"} & set(block)) > 1
    print(f"  {mixed} of {total}")


if __name__ == "__main__":
    main()
