"""The experiment: does binding data stop models putting wrong numbers on screen?

Each task is a request plus a data model. Every task runs in two arms:

  inline  the model sees the values and types them into the UI.
  bound   the model sees only the paths and types, and binds to them.

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


def figure_errors(messages: list[Any], data: Any) -> tuple[int, int]:
    """(figures shown, figures shown that are not in the source data)."""
    shown = wrong = 0
    truth = source_figures(data)
    model = data_of(messages, data)
    for components in components_by_surface(messages).values():
        for value in _shown_values(components, model):
            figures = [str(value)] if isinstance(value, (int, float)) else figures_in(str(value))
            for figure in figures:
                shown += 1
                if _normal(figure) not in truth:
                    wrong += 1
    return shown, wrong


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
    error: str | None


def one_run(model: str, task: str, arm: str, trial: int, llm: Any = None) -> Run:
    request, data = TASKS[task]
    bound = arm == "bound"
    started = time.monotonic()
    result = generate(request, llm or model, data=data, bound=bound, attempts=3)
    seconds = time.monotonic() - started
    first = result.attempts[0] if result.attempts else None
    shown = wrong = 0
    final = result.messages
    if not result.ok and result.attempts:
        # Score what the last attempt would have shown had nothing stopped it.
        final = repair(result.attempts[-1].reply).messages
    if final:
        shown, wrong = figure_errors(final, data)
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
        shown,
        wrong,
        result.error,
    )


def run(models: Iterable[str], trials: int, out: Path, tasks: Iterable[str] | None = None) -> None:
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
                    for arm in ("inline", "bound"):
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
            "median_seconds": statistics.median(r["seconds"] for r in runs),
        }
    return out


__all__ = ["TASKS", "figure_errors", "run", "source_figures", "summarise", "one_run", "system_prompt", "validate"]
