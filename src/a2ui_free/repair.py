"""Turns almost-right model output into A2UI v1.0, without another model call.

Models fail in predictable ways. Each repair here undoes one of them, and every
repair is reported. Repairs only change structure: they never touch what a value
says, so a repair cannot invent content. `repair` is idempotent and never
raises. Anything it cannot fix is left for `validate` to refuse.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .validate import BASIC_CATALOG_ID

_FENCE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n(.*?)\n?```", re.S)
_THINKING = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.S | re.I)
_ENVELOPES = ("createSurface", "updateComponents", "updateDataModel", "deleteSurface")
_CHILD_KEYS = ("child", "trigger", "content")


@dataclass
class Repaired:
    messages: Any
    notes: list[str] = field(default_factory=list)


def _first_json(text: str) -> Any:
    """The first complete JSON value in text, or None."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", text):
        try:
            value, _ = decoder.raw_decode(text[match.start() :])
        except ValueError:
            continue
        if isinstance(value, (dict, list)):
            return value
    return None


def _trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def extract_json(text: str, notes: list[str] | None = None) -> Any:
    """Finds the JSON in a model's reply, around any prose or fences."""
    notes = notes if notes is not None else []
    cleaned = _THINKING.sub("", text)
    if cleaned != text:
        notes.append("removed visible reasoning")
    fenced = _FENCE.findall(cleaned)
    candidates = (fenced + [cleaned]) if fenced else [cleaned]
    if fenced:
        notes.append("took JSON from a code fence")
    for candidate in candidates:
        for attempt, label in ((candidate, None), (_trailing_commas(candidate), "removed trailing commas")):
            value = _first_json(attempt)
            if value is not None:
                if label:
                    notes.append(label)
                return value
    return None


def _unwrap_values(node: Any, notes: list[str]) -> Any:
    """{"value": "x"} and v0.8's {"literalString": "x"} become "x"."""
    if isinstance(node, list):
        return [_unwrap_values(n, notes) for n in node]
    if isinstance(node, dict):
        if len(node) == 1:
            key, value = next(iter(node.items()))
            if key in ("value", "literalString", "literalNumber", "literalBoolean") and not isinstance(value, (dict, list)):
                notes.append(f"unwrapped {key}")
                return value
            if key == "path" and isinstance(value, str):
                notes.append("renamed path to @path")
                return {"@path": value}
        return {k: _unwrap_values(v, notes) for k, v in node.items()}
    return node


def _new_id(base: str, taken: set[str]) -> str:
    n, candidate = 1, base
    while candidate in taken:
        n += 1
        candidate = f"{base}-{n}"
    taken.add(candidate)
    return candidate


def _flatten(components: list[Any], notes: list[str]) -> list[dict]:
    """Nested component definitions become flat siblings referenced by id."""
    flat: list[dict] = []
    taken = {c["id"] for c in components if isinstance(c, dict) and isinstance(c.get("id"), str)}

    def lift(item: Any) -> Any:
        if not (isinstance(item, dict) and isinstance(item.get("component"), (str, dict))):
            return item
        cid = item.get("id") if isinstance(item.get("id"), str) else None
        if cid is None:
            kind = item["component"] if isinstance(item["component"], str) else "part"
            cid = _new_id(kind.lower(), taken)
            item = {**item, "id": cid}
        visit(item)
        notes.append(f"moved nested {cid} up to a sibling")
        return cid

    def visit(component: dict) -> None:
        children = component.get("children")
        if isinstance(children, list):
            component["children"] = [lift(c) for c in children]
        elif isinstance(children, dict) and "component" in children:
            component["children"] = [lift(children)]
        for key in _CHILD_KEYS:
            if isinstance(component.get(key), dict) and "component" in component[key]:
                component[key] = lift(component[key])
        flat.append(component)

    for component in components:
        if isinstance(component, dict):
            visit(component)
    return flat


def _old_shape(component: dict, notes: list[str]) -> dict:
    """v0.8 wrote {"id": "a", "component": {"Text": {...}}}."""
    inner = component.get("component")
    if isinstance(inner, dict) and len(inner) == 1:
        kind, props = next(iter(inner.items()))
        if isinstance(props, dict):
            notes.append(f"rewrote old style {kind}")
            return {"id": component.get("id"), "component": kind, **props}
    return component


def _clean_components(raw: list[Any], notes: list[str]) -> list[dict]:
    shaped = [_old_shape(c, notes) if isinstance(c, dict) else c for c in raw]
    shaped = [_unwrap_values(c, notes) for c in shaped]
    flat = _flatten(shaped, notes)
    seen: set[str] = set()
    kept: list[dict] = []
    for component in flat:
        cid = component.get("id")
        if not isinstance(cid, str) or not isinstance(component.get("component"), str):
            notes.append("dropped a component with no id or type")
            continue
        if cid in seen:
            notes.append(f"dropped a second {cid}")
            continue
        seen.add(cid)
        kept.append(component)
    if kept and "root" not in seen:
        referenced = set()
        for component in kept:
            children = component.get("children")
            if isinstance(children, list):
                referenced.update(c for c in children if isinstance(c, str))
            for key in _CHILD_KEYS:
                if isinstance(component.get(key), str):
                    referenced.add(component[key])
        tops = [c for c in kept if c["id"] not in referenced]
        if len(tops) == 1:
            notes.append(f"made {tops[0]['id']} the root")
            old = tops[0]["id"]
            tops[0]["id"] = "root"
            for component in kept:
                for key in _CHILD_KEYS:
                    if component.get(key) == old:
                        component[key] = "root"
    return kept


def _as_messages(value: Any, notes: list[str]) -> list[Any]:
    """Whatever came back, as a list of single-purpose messages."""
    if isinstance(value, dict):
        for wrapper in ("messages", "a2ui", "ui", "response", "output"):
            if isinstance(value.get(wrapper), list):
                notes.append(f"unwrapped {wrapper}")
                return _as_messages(value[wrapper], notes)
        if any(k in value for k in _ENVELOPES):
            value = [value]
        elif isinstance(value.get("components"), list):
            notes.append("wrapped a bare component list")
            value = [{"createSurface": value}]
        else:
            return [value]
    if not isinstance(value, list):
        return []
    if value and all(isinstance(c, dict) and "component" in c for c in value):
        notes.append("wrapped a bare component list")
        return [{"createSurface": {"components": value}}]
    messages: list[Any] = []
    for item in value:
        if isinstance(item, dict) and sum(k in item for k in _ENVELOPES) > 1:
            notes.append("split a message doing two jobs")
            for key in _ENVELOPES:
                if key in item:
                    messages.append({"version": item.get("version"), key: item[key]})
        elif isinstance(item, dict) and any(k in item for k in _ENVELOPES):
            messages.append(item)
        else:
            notes.append("dropped something that is not a message")
    return messages


def _join_surface(messages: list[Any], notes: list[str]) -> list[Any]:
    """createSurface then updateComponents for one surface become one message."""
    creating = [m for m in messages if isinstance(m, dict) and "createSurface" in m]
    updating = [m for m in messages if isinstance(m, dict) and "updateComponents" in m]
    if len(creating) == 1 and len(updating) >= 1 and len(messages) == 1 + len(updating):
        body = creating[0]["createSurface"]
        if isinstance(body, dict) and not body.get("components"):
            parts: list[Any] = []
            for message in updating:
                part = message["updateComponents"]
                if isinstance(part, dict) and isinstance(part.get("components"), list):
                    parts.extend(part["components"])
            if parts:
                notes.append("joined updateComponents into createSurface")
                return [{**creating[0], "createSurface": {**body, "components": parts}}]
    return messages


def repair(reply: Any, *, surface_id: str = "main", catalog_id: str = BASIC_CATALOG_ID) -> Repaired:
    """Repairs a model's reply, given as text or as parsed JSON."""
    notes: list[str] = []
    try:
        value = extract_json(reply, notes) if isinstance(reply, str) else copy.deepcopy(reply)
        if value is None:
            return Repaired([], [*notes, "no JSON found"])
        messages = _join_surface(_as_messages(value, notes), notes)
        if not any(isinstance(m, dict) and "createSurface" in m for m in messages):
            update = next((m for m in messages if isinstance(m, dict) and "updateComponents" in m), None)
            if update is not None:
                notes.append("added the missing createSurface")
                messages.insert(0, {"createSurface": {"surfaceId": update["updateComponents"].get("surfaceId", surface_id)}})
                messages = _join_surface(messages, notes)
        for message in messages:
            if not isinstance(message, dict):
                continue
            if message.get("version") != "1.0":
                if "version" in message:
                    notes.append(f"set version from {message['version']!r} to 1.0")
                message["version"] = "1.0"
            created = message.get("createSurface")
            if isinstance(created, dict):
                created.setdefault("surfaceId", surface_id)
                created.setdefault("catalogId", catalog_id)
                if "theme" in created:
                    del created["theme"]
                    notes.append("removed theme, which v1.0 forbids")
            for key in ("createSurface", "updateComponents"):
                body = message.get(key)
                if isinstance(body, dict) and isinstance(body.get("components"), list):
                    body["components"] = _clean_components(body["components"], notes)
        return Repaired(messages, notes)
    except Exception as error:  # Repair is best effort: validate gets the last word.
        return Repaired(reply if isinstance(reply, list) else [], [*notes, f"repair stopped: {type(error).__name__}"])
