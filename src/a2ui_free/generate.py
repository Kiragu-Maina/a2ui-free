"""Asks a model for UI, and only returns UI that passed every check."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .llm import Chain, LlmError, chain
from .repair import repair
from .validate import BASIC_CATALOG_ID, Report, basic_catalog, validate


def catalog_cheatsheet(catalog: dict | None = None) -> str:
    """A compact list of components, written from the catalog so it cannot drift."""
    catalog = catalog or basic_catalog()
    lines = []
    for name, spec in catalog.get("components", {}).items():
        parts = spec.get("allOf", [spec])
        props: dict = {}
        required: set[str] = set()
        for part in parts:
            if isinstance(part, dict):
                props.update(part.get("properties", {}))
                required.update(part.get("required", []))
        shown = [
            f"{key}{'' if key in required else '?'}" + (f"({'|'.join(value['enum'])})" if "enum" in value else "")
            for key, value in props.items()
            if key != "component"
        ]
        lines.append(f"- {name}: {', '.join(shown)}")
    return "\n".join(lines)


def data_shape(data: Any, prefix: str = "") -> list[str]:
    """The paths of a data model with their types. No values: that is the point."""
    if isinstance(data, dict):
        lines: list[str] = []
        for key, value in data.items():
            lines.extend(data_shape(value, f"{prefix}/{key}"))
        return lines
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            fields = ", ".join(f"{k} ({type(v).__name__})" for k, v in data[0].items())
            return [f"{prefix}: list of rows, each with {fields}"]
        return [f"{prefix}: list"]
    kind = "number" if isinstance(data, (int, float)) and not isinstance(data, bool) else type(data).__name__
    return [f"{prefix}: {kind}"]


RULES = """You write user interfaces as A2UI v1.0 JSON. Reply with JSON only.

Reply with a list holding one message:
[{"version": "1.0", "createSurface": {"surfaceId": "main",
  "catalogId": "%s",
  "components": [ ... ]}}]

Rules for components:
- The list is flat. Every component has a unique "id" and a "component" name.
- One component has the id "root". Every other component is reached from it.
- Children are ids, never nested objects: "children": ["title", "total"].
- "child" holds one id.
- Text shows words through its "text" field. Only layout components have children.
- A property named with ? may be left out.
- Use only these components:
%s

A complete example, for a card with a heading and one line:
[{"version": "1.0", "createSurface": {"surfaceId": "main",
  "catalogId": "%s",
  "components": [
    {"id": "root", "component": "Card", "child": "body"},
    {"id": "body", "component": "Column", "children": ["heading", "line"]},
    {"id": "heading", "component": "Text", "text": "A heading"},
    {"id": "line", "component": "Text", "text": "One line of words"}
  ]}}]
"""

BOUND = """
You are not told the data. It is filled in by the app, and you never see a value.
Show data only by binding: {"@path": "/invoice/total"}.
Never type a number, price, date or name from the data into the UI. Bind it.
A label such as "Total" is plain text. The value next to it is a binding.
For a list of rows use
  {"id": "rows", "component": "List", "children": {"componentId": "row", "path": "/items"}}
and inside the "row" component bind with a relative path: {"@path": "name"}.

The data model has exactly these paths:
%s
"""

INLINE = """
Here is the data to show. Write the values directly into the components as text.

%s
"""


def system_prompt(*, data: Any, bound: bool, catalog: dict | None = None) -> str:
    catalog_id = (catalog or {}).get("catalogId", BASIC_CATALOG_ID)
    head = RULES % (catalog_id, catalog_cheatsheet(catalog), catalog_id)
    if data is None:
        return head
    if bound:
        return head + BOUND % "\n".join(data_shape(data))
    return head + INLINE % json.dumps(data, indent=1, ensure_ascii=False)


@dataclass
class Attempt:
    model: str
    reply: str
    notes: list[str]
    report_after_repair: Report
    first_try_valid: bool


@dataclass
class Result:
    ok: bool
    messages: list[Any]
    report: Report
    attempts: list[Attempt] = field(default_factory=list)
    error: str | None = None


def _feedback(report: Report) -> str:
    lines = "\n".join(f"- {issue}" for issue in report.issues[:12])
    return f"\n\nYour last reply was refused for these reasons. Fix them and reply with the full JSON:\n{lines}"


def generate(
    request: str,
    llm: Any = None,
    *,
    data: Any = None,
    bound: bool = True,
    catalog: dict | None = None,
    attempts: int = 3,
) -> Result:
    """Makes UI for `request`. With `bound`, the model sees the shape of `data`
    and never its values, and any figure it types in is refused."""
    models: Chain = chain(llm)
    system = system_prompt(data=data, bound=bound, catalog=catalog)
    catalog_id = (catalog or {}).get("catalogId", BASIC_CATALOG_ID)
    strict = bound and data is not None
    prompt = request
    log: list[Attempt] = []
    last = Report()
    for _ in range(max(1, attempts)):
        try:
            reply = models.complete(system, prompt)
        except LlmError as error:
            return Result(False, [], last, log, str(error))
        fixed = repair(reply, catalog_id=catalog_id)
        checks = dict(catalog=catalog, data=data, check_bindings=data is not None, no_literal_figures=strict)
        last = validate(fixed.messages, **checks)
        log.append(Attempt(models.used, reply, fixed.notes, last, _valid_as_sent(reply, checks)))
        if last.ok:
            messages = fixed.messages
            if strict:
                messages = with_data(messages, data)
            return Result(True, messages, last, log)
        prompt = request + _feedback(last)
    return Result(False, [], last, log, "no valid UI after every attempt")


def _valid_as_sent(reply: str, checks: dict) -> bool:
    """Whether the reply would have passed with no repair at all."""
    try:
        return validate(json.loads(reply), **checks).ok
    except (TypeError, ValueError):
        return False


def with_data(messages: list[Any], data: Any) -> list[Any]:
    """The app's real data goes in here, after every check, past the model."""
    out = []
    for message in messages:
        created = message.get("createSurface") if isinstance(message, dict) else None
        if isinstance(created, dict):
            out.append({**message, "createSurface": {**created, "dataModel": data}})
        else:
            out.append(message)
    return out
