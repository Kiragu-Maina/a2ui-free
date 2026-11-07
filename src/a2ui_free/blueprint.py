"""Asks the model for less: a small plan, which code expands into A2UI.

Writing a flat component graph with ids, children and list templates is hard for
a small model. Choosing what to show is not. Here the model answers with a plan
of a few lines, and `expand` turns it into A2UI that is valid by construction.
The model still never sees a value, and now it never writes a component either.

    {"title": "Invoice",
     "blocks": [
       {"fields": [{"label": "Customer", "path": "/customer"}]},
       {"table": {"path": "/lines",
                  "columns": [{"label": "Item", "field": "item"}]}}]}

`read_plan` is strict and never raises: a path that does not exist, a label with
a figure in it, or an unknown block is reported, so the model can try again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .generate import Attempt, Result, data_shape, with_data
from .llm import Chain, LlmError, chain
from .repair import extract_json
from .validate import BASIC_CATALOG_ID, Report, figures_in, resolve_pointer, validate

MAX_BLOCKS = 12
MAX_ITEMS = 40

PROMPT = """You choose what a screen shows. Reply with JSON only, in this form:

{"title": "A short title",
 "blocks": [
   {"heading": "A short section heading"},
   {"fields": [{"label": "Words for the reader", "path": "/a/path"}]},
   {"table": {"path": "/a/list", "columns": [{"label": "Words", "field": "a_field"}]}}
 ]}

Rules:
- "fields" shows single values. Use a path from the list below, exactly as written.
- "table" shows a list of rows. "field" is a name inside each row.
- Labels and headings are words only. Never write a number, price or date. The app
  fills in every value. You will never see them.

The data has exactly these paths:
%s
"""


@dataclass
class Plan:
    title: str
    blocks: list[dict] = field(default_factory=list)


def _text(value: Any, report: Report, where: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        report.add("plan", where, "needs some words")
        return None
    flat = " ".join(value.split())[:120]
    if figures_in(flat):
        report.add("plan", where, f"{flat!r} has a figure in it: labels are words only")
        return None
    return flat


def read_plan(value: Any, data: Any) -> tuple[Plan | None, Report]:
    """Checks a plan against the data. Bad blocks are refused, never guessed at."""
    report = Report()
    if not isinstance(value, dict):
        report.add("plan", "/", "expected one JSON object")
        return None, report
    title = _text(value.get("title"), report, "title") or ""
    blocks: list[dict] = []
    raw = value.get("blocks")
    if not isinstance(raw, list) or not raw:
        report.add("plan", "blocks", "needs at least one block")
        return None, report
    for i, block in enumerate(raw[:MAX_BLOCKS]):
        where = f"blocks/{i}"
        if not isinstance(block, dict):
            report.add("plan", where, "must be an object")
            continue
        parts = [key for key in ("heading", "fields", "table") if key in block]
        if not parts:
            report.add("plan", where, "must be a heading, fields or table")
        # Models often put a heading, fields and a table in one block. Each part
        # is read on its own, in reading order, so none of it is lost unseen.
        if "heading" in block:
            words = _text(block["heading"], report, where)
            if words:
                blocks.append({"heading": words})
        if "fields" in block:
            items = []
            for j, item in enumerate(block["fields"] if isinstance(block["fields"], list) else []):
                if not isinstance(item, dict):
                    continue
                label = _text(item.get("label"), report, f"{where}/{j}")
                path = item.get("path")
                found, target = resolve_pointer(data, path) if isinstance(path, str) else (False, None)
                if not found or isinstance(target, (dict, list)):
                    report.add("plan", f"{where}/{j}", f"{path!r} is not a value in the data")
                elif label:
                    items.append({"label": label, "path": path})
            if items:
                blocks.append({"fields": items[:MAX_ITEMS]})
        if "table" in block:
            table = block["table"] if isinstance(block["table"], dict) else {}
            path = table.get("path")
            found, rows = resolve_pointer(data, path) if isinstance(path, str) else (False, None)
            if not found or not isinstance(rows, list) or not rows:
                report.add("plan", where, f"{path!r} is not a list in the data")
                continue
            columns = []
            for j, column in enumerate(table.get("columns") if isinstance(table.get("columns"), list) else []):
                if not isinstance(column, dict):
                    continue
                label = _text(column.get("label"), report, f"{where}/{j}")
                name = column.get("field")
                if not isinstance(name, str) or not all(isinstance(r, dict) and name in r for r in rows):
                    report.add("plan", f"{where}/{j}", f"{name!r} is not in every row of {path}")
                elif label:
                    columns.append({"label": label, "field": name})
            if columns:
                blocks.append({"table": {"path": path, "columns": columns[:MAX_ITEMS]}})
    if not blocks:
        report.add("plan", "blocks", "no usable block")
        return None, report
    if not any("fields" in block or "table" in block for block in blocks):
        report.add("plan", "blocks", "shows no data: add fields or a table")
        return None, report
    return Plan(title, blocks), report


def expand(plan: Plan, *, surface_id: str = "main", catalog_id: str = BASIC_CATALOG_ID) -> list[dict]:
    """A plan as A2UI v1.0. Only this code writes components."""
    components: list[dict] = []
    top: list[str] = []
    count = 0

    def add(kind: str, **props: Any) -> str:
        nonlocal count
        count += 1
        cid = f"c{count}"
        components.append({"id": cid, "component": kind, **props})
        return cid

    if plan.title:
        top.append(add("Text", text=plan.title))
    for block in plan.blocks:
        if "heading" in block:
            top.append(add("Text", text=block["heading"]))
        elif "fields" in block:
            for item in block["fields"]:
                label = add("Text", text=item["label"])
                value = add("Text", text={"@path": item["path"]})
                top.append(add("Row", children=[label, value], justify="spaceBetween"))
        else:
            table = block["table"]
            header = [add("Text", text=c["label"], variant="caption") for c in table["columns"]]
            top.append(add("Row", children=header, justify="spaceBetween"))
            cells = [add("Text", text={"@path": c["field"]}) for c in table["columns"]]
            row = add("Row", children=cells, justify="spaceBetween")
            top.append(add("List", children={"componentId": row, "path": table["path"]}))
    components.insert(0, {"id": "root", "component": "Column", "children": top})
    return [
        {
            "version": "1.0",
            "createSurface": {"surfaceId": surface_id, "catalogId": catalog_id, "components": components},
        }
    ]


def generate_from_plan(request: str, llm: Any, data: Any, *, attempts: int = 3) -> Result:
    """Like generate, with the model choosing and the code building."""
    models: Chain = chain(llm)
    system = PROMPT % "\n".join(data_shape(data))
    prompt = request
    log: list[Attempt] = []
    last = Report()
    for _ in range(max(1, attempts)):
        try:
            reply = models.complete(system, prompt)
        except LlmError as error:
            return Result(False, [], last, log, str(error))
        plan, last = read_plan(extract_json(reply), data)
        messages = expand(plan) if plan else []
        if plan:
            built = validate(messages, data=data, no_literal_figures=True)
            last.issues.extend(built.issues)
        log.append(Attempt(models.used, reply, [], last, last.ok))
        if plan and last.ok:
            return Result(True, with_data(messages, data), last, log)
        lines = "\n".join(f"- {issue}" for issue in last.issues[:12])
        prompt = f"{request}\n\nYour last reply was refused for these reasons. Fix them:\n{lines}"
    return Result(False, [], last, log, "no valid plan after every attempt")
