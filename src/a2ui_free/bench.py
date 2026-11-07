"""The experiment: does binding data stop models putting wrong numbers on screen?

Each task is a request plus a data model. Every task runs in three arms:

  inline     the model sees the values and writes A2UI with them typed in.
  bound      the model sees only paths and writes A2UI that binds to them.
  blueprint  the model sees only paths and writes a small plan. Code builds the A2UI.

For each run this records whether the raw reply was valid, whether repair saved
it, how many attempts it took, and, for the rendered result, every figure the
user would read that is not in the source data.

Run: a2ui-free bench --models ollama/llama3.2:3b --trials 3 --out results.jsonl
"""

from __future__ import annotations

import json
import re
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .blueprint import generate_from_plan
from .generate import generate, system_prompt
from .llm import LlmError, chain
from .repair import repair
from .validate import _template_ids, components_by_surface, data_of, figures_in, resolve_pointer, validate

TASKS: dict[str, tuple[str, dict]] = {
    "invoice": (
        "Show this invoice: who it is for, each line with its quantity and amount, and the total due.",
        {
            "customer": "Wanjiru Hardware",
            "number": "INV-20418",
            "lines": [
                {"item": "Roofing nails", "qty": 12, "amount": 1740.50},
                {"item": "Cement bags", "qty": 8, "amount": 6960.00},
                {"item": "Paint tins", "qty": 3, "amount": 4275.75},
            ],
            "tax": 2076.40,
            "total": 15052.65,
        },
    ),
    "budget": (
        "Show how this month's spending compares with the budget, for each category.",
        {
            "month": "October",
            "budget": 48250,
            "spent": 51377,
            "categories": [
                {"name": "Food", "budget": 16500, "spent": 18214},
                {"name": "Transport", "budget": 7250, "spent": 6891},
                {"name": "Rent", "budget": 18000, "spent": 18000},
                {"name": "Going out", "budget": 6500, "spent": 8272},
            ],
        },
    ),
    "weather": (
        "Show the forecast for the next three days.",
        {
            "city": "Nairobi",
            "days": [
                {"day": "Monday", "high": 26.4, "low": 14.2, "rain": 35},
                {"day": "Tuesday", "high": 24.8, "low": 15.1, "rain": 72},
                {"day": "Wednesday", "high": 27.3, "low": 13.6, "rain": 18},
            ],
        },
    ),
    "orders": (
        "Show a dashboard of today's shop orders with the headline numbers and the three biggest orders.",
        {
            "orders_today": 147,
            "revenue": 389204,
            "refunds": 2318,
            "average_order": 2647.65,
            "top": [
                {"customer": "Otieno Stores", "value": 41200},
                {"customer": "Mama Njeri", "value": 28975},
                {"customer": "Kamau Traders", "value": 17630},
            ],
        },
    ),
    "account": (
        "Show this account summary: balance, what is owed, and the last three payments.",
        {
            "holder": "Achieng Odhiambo",
            "balance": 7342.18,
            "owed": 1250.00,
            "payments": [
                {"to": "KPLC Prepaid", "amount": 2500, "date": "2026-10-28"},
                {"to": "Safaricom", "amount": 1000, "date": "2026-10-30"},
                {"to": "Naivas", "amount": 3186, "date": "2026-11-02"},
            ],
        },
    ),
    "fleet": (
        "Show the status of these vehicles with fuel level and distance today.",
        {
            "fleet": "Coast Haulage",
            "vehicles": [
                {"plate": "KDA 412G", "fuel": 62, "distance": 318.4},
                {"plate": "KCZ 907H", "fuel": 17, "distance": 96.2},
                {"plate": "KDB 155K", "fuel": 88, "distance": 441.9},
            ],
        },
    ),
}

ARMS = ("inline", "bound", "blueprint")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _normal(figure: str) -> str:
    """1,200.50 and 1200.5 are the same figure."""
    cleaned = figure.replace(",", "").rstrip("%").strip()
    try:
        return f"{float(cleaned):g}"
    except ValueError:
        return cleaned


def source_figures(data: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(data, dict):
        for value in data.values():
            found |= source_figures(value)
    elif isinstance(data, list):
        for value in data:
            found |= source_figures(value)
    elif isinstance(data, bool):
        pass
    elif isinstance(data, (int, float)):
        found.add(_normal(str(data)))
    elif isinstance(data, str):
        found |= {_normal(m) for m in _NUMBER.findall(data)}
    return found


def _shown_values(components: list[dict], model: dict):
    """Every string or number the user would read, after bindings are resolved."""
    templates = _template_ids(components)
    for component in components:
        scope = templates.get(component.get("id"))
        for key in ("text", "label", "value", "title", "description"):
            value = component.get(key)
            if isinstance(value, dict) and "@path" in value:
                path = value["@path"]
                if path.startswith("/"):
                    found, resolved = resolve_pointer(model, path)
                    if found:
                        yield resolved
                elif scope:
                    found, rows = resolve_pointer(model, scope)
                    if found and isinstance(rows, list):
                        for row in rows:
                            ok, resolved = resolve_pointer(row, "/" + path)
                            if ok:
                                yield resolved
            elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
                yield value


def shown_figures(messages: list[Any], data: Any) -> list[str]:
    """Every figure a reader would see, normalised."""
    model = data_of(messages, data)
    out: list[str] = []
    for components in components_by_surface(messages).values():
        for value in _shown_values(components, model):
            figures = [str(value)] if isinstance(value, (int, float)) else figures_in(str(value))
            out.extend(_normal(figure) for figure in figures)
    return out


def figure_errors(messages: list[Any], data: Any) -> tuple[int, int]:
    """(figures shown, figures shown that are not in the source data)."""
    truth = source_figures(data)
    shown = shown_figures(messages, data)
    return len(shown), sum(figure not in truth for figure in shown)


def explain_figure(figure: str, data: Any) -> str:
    """Why a figure is not in the source. `figure_errors` counts every figure the
    source does not hold, but a correct sum and an invented number are different
    failures. "derived" means it equals the gap between two source figures or the
    sum of two. Anything else is "unsupported". Only the reader can say whether a
    derived figure is the one the screen claims it is."""
    source = [float(f) for f in source_figures(data)]
    target = float(_normal(figure))
    for i, a in enumerate(source):
        for b in source[i + 1 :]:
            if abs(abs(a - b) - target) < 0.005 or abs(a + b - target) < 0.005:
                return "derived"
    return "unsupported"


def coverage(messages: list[Any], data: Any) -> float:
    """The share of the source's figures that reach the screen. A surface can be
    valid and accurate by showing nothing, so accuracy alone proves little."""
    truth = source_figures(data)
    return len(truth & set(shown_figures(messages, data))) / len(truth) if truth else 1.0


@dataclass
class Run:
    model: str
    task: str
    arm: str
    trial: int
    seconds: float
    raw_valid: bool
    repaired_valid: bool
    final_valid: bool
    attempts: int
    repairs: list[str]
    issues: list[str]
    figures_shown: int
    figures_wrong: int
    wrong_figures: list[str]
    coverage: float
    error: str | None
    replies: list[str]


def one_run(model: str, task: str, arm: str, trial: int, llm: Any = None) -> Run:
    request, data = TASKS[task]
    started = time.monotonic()
    if arm == "blueprint":
        result = generate_from_plan(request, llm or model, data, attempts=3)
    else:
        result = generate(request, llm or model, data=data, bound=arm == "bound", attempts=3)
    seconds = time.monotonic() - started
    first = result.attempts[0] if result.attempts else None
    final = result.messages
    if not result.ok and result.attempts and arm != "blueprint":
        # Score what the last attempt would have shown had nothing stopped it.
        final = repair(result.attempts[-1].reply).messages
    truth = source_figures(data)
    shown = shown_figures(final, data) if final else []
    wrong = [figure for figure in shown if figure not in truth]
    return Run(
        model,
        task,
        arm,
        trial,
        round(seconds, 1),
        bool(first and first.first_try_valid),
        bool(first and first.report_after_repair.ok),
        result.ok,
        len(result.attempts),
        first.notes if first else [],
        sorted({i.code for i in result.report.issues}),
        len(shown),
        len(wrong),
        wrong,
        round(coverage(final, data), 3) if final else 0.0,
        result.error,
        [a.reply for a in result.attempts],
    )


def run(
    models: Iterable[str],
    trials: int,
    out: Path,
    tasks: Iterable[str] | None = None,
    arms: Iterable[str] | None = None,
) -> None:
    """Appends one JSON line per run, so a long run can stop and resume."""
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            done.add((r["model"], r["task"], r["arm"], r["trial"]))
    with out.open("a", encoding="utf-8") as handle:
        for model in models:
            for trial in range(trials):
                for task in tasks or TASKS:
                    for arm in arms or ARMS:
                        if (model, task, arm, trial) in done:
                            continue
                        try:
                            result = one_run(model, task, arm, trial)
                        except LlmError as error:
                            print(f"{model} {task} {arm}: {error}", flush=True)
                            continue
                        handle.write(json.dumps(result.__dict__) + "\n")
                        handle.flush()
                        print(f"{model} {task} {arm} {trial}: valid={result.final_valid} "
                              f"wrong={result.figures_wrong}/{result.figures_shown} {result.seconds}s", flush=True)


def summarise(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault((row["model"], row["arm"]), []).append(row)
        groups.setdefault(("all", row["arm"]), []).append(row)
    out = {}
    for (model, arm), runs in sorted(groups.items()):
        n = len(runs)
        shown = sum(r["figures_shown"] for r in runs)
        wrong = sum(r["figures_wrong"] for r in runs)
        out[f"{model} | {arm}"] = {
            "runs": n,
            "raw_valid": sum(r["raw_valid"] for r in runs) / n,
            "after_repair": sum(r["repaired_valid"] for r in runs) / n,
            "after_retry": sum(r["final_valid"] for r in runs) / n,
            "runs_with_a_wrong_figure": sum(r["figures_wrong"] > 0 for r in runs) / n,
            "figures_shown": shown,
            "figures_wrong": wrong,
            "wrong_rate": wrong / shown if shown else 0,
            "median_coverage_of_valid": statistics.median([r["coverage"] for r in runs if r["final_valid"]] or [0]),
            "valid_and_at_least_half_shown": sum(r["final_valid"] and r["coverage"] >= 0.5 for r in runs) / n,
            "median_seconds": statistics.median(r["seconds"] for r in runs),
        }
    return out


__all__ = ["ARMS", "TASKS", "coverage", "explain_figure", "figure_errors", "run", "source_figures", "summarise", "one_run", "system_prompt", "validate"]
