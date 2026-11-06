"""Decides whether a list of A2UI v1.0 messages is safe to hand to a renderer.

Four layers, cheapest thought first. None of them raise: a model's output is
untrusted input, so the answer to bad input is a Report, never an exception.

1. Schema: the official v1.0 JSON schemas and the chosen catalog.
2. Graph: what a schema cannot say. Every child exists, ids are unique, a root
   exists, nothing loops.
3. Bindings: every `{"@path": ...}` points at something in the data model.
4. Figures: with `no_literal_figures`, no number is typed into the UI. Numbers
   must arrive through a binding, so a model cannot show one it made up.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

BASIC_CATALOG_ID = "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json"
_BASE = "https://a2ui.org/specification/v1_0/"

# A figure is a number a reader would take as a fact: 12, 3.5, 1,200, 40%.
# Ordinals in ordinary prose ("Step 1") are short and are allowed through.
_FIGURE = re.compile(r"(?<![\w.,])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d{2,}(?![\w])|\d+\s?%)(?:\s?%)?")


@dataclass(frozen=True)
class Issue:
    code: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.code} at {self.where}: {self.message}"


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def codes(self) -> set[str]:
        return {i.code for i in self.issues}

    def add(self, code: str, where: str, message: str) -> None:
        self.issues.append(Issue(code, where, message))

    def __str__(self) -> str:
        return "valid" if self.ok else "\n".join(str(i) for i in self.issues)


# The spec's one Unicode identifier pattern, in the only form Python's re reads.
# XID_Start or _, then XID_Continue, is what \\w gives without a leading digit.
_UNICODE_IDENTIFIER = "^[\\\\p{XID_Start}_][\\\\p{XID_Continue}]*$"
_PYTHON_IDENTIFIER = "^[^\\\\W\\\\d]\\\\w*$"


def _load(name: str) -> dict:
    text = resources.files("a2ui_free.schemas").joinpath(name).read_text(encoding="utf-8")
    return json.loads(text.replace(_UNICODE_IDENTIFIER, _PYTHON_IDENTIFIER))


def _compose_catalog(catalog: dict) -> dict:
    """Builds the `anyComponent` definition the envelope schema refers to."""
    composed = dict(catalog)
    defs = dict(composed.get("$defs", {}))
    components = composed.get("components", {})
    defs["anyComponent"] = {
        "oneOf": [{"$ref": f"#/components/{name}"} for name in components],
        "discriminator": {"propertyName": "component"},
    }
    composed["$defs"] = defs
    composed["$id"] = _BASE + "catalog.json"
    return composed


_FORMATS = FormatChecker(formats=())


@_FORMATS.checks("uri")
def _is_uri(value: object) -> bool:
    return not isinstance(value, str) or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:\S*$", value) is not None


@lru_cache(maxsize=8)
def _validator(catalog_json: str):
    envelope = _load("agent_to_renderer.json")
    common = _load("common_types.json")
    catalog = _compose_catalog(json.loads(catalog_json))
    registry = Registry().with_resources(
        (doc["$id"], Resource.from_contents(doc, default_specification=_draft()))
        for doc in (envelope, common, catalog)
    )
    return Draft202012Validator(envelope, registry=registry, format_checker=_FORMATS)


def _draft():
    from referencing.jsonschema import DRAFT202012

    return DRAFT202012


def basic_catalog() -> dict:
    return _load("basic_catalog.json")


def _pointer(path: Iterable[Any]) -> str:
    return "/" + "/".join(str(p) for p in path)


def schema_valid(message: Any, catalog: dict | None = None) -> bool:
    """Layer 1 alone: one message against the official schemas."""
    return _validator(json.dumps(catalog or basic_catalog(), sort_keys=True)).is_valid(message)


def _schema_issues(message: Any, index: int, catalog_json: str, report: Report) -> None:
    names = list(json.loads(catalog_json).get("components", {}))
    errors = sorted(_validator(catalog_json).iter_errors(message), key=lambda e: str(list(e.absolute_path)))
    for error in errors[:6]:
        detail = _useful(error, names)
        report.add("schema", f"message {index}{_pointer(error.absolute_path)}", detail[:220])


def _useful(error, names: list[str]) -> str:
    """oneOf failures read badly, and a model has to act on this text."""
    instance = error.instance
    if error.validator == "oneOf" and error.context:
        if isinstance(instance, dict) and isinstance(instance.get("component"), str):
            name = instance["component"]
            if name not in names:
                return f"{name} is not a component in this catalog"
            # Each branch of the union is one component, in catalog order.
            own = [e for e in error.context if e.relative_schema_path and e.relative_schema_path[0] == names.index(name)]
            if own:
                return f"{name}: {_useful(own[0], names)}"
        best = max(error.context, key=lambda e: (len(list(e.absolute_path)), e.validator != "required"))
        return _useful(best, names)
    return error.message


def components_by_surface(messages: list[Any]) -> dict[str, list[dict]]:
    surfaces: dict[str, list[dict]] = {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        body = message.get("createSurface") or message.get("updateComponents")
        if isinstance(body, dict) and isinstance(body.get("components"), list):
            surface = str(body.get("surfaceId", ""))
            # A component with no usable id is a schema failure, reported there.
            surfaces.setdefault(surface, []).extend(
                c for c in body["components"] if isinstance(c, dict) and isinstance(c.get("id"), str)
            )
    return surfaces


def _children_of(component: dict) -> list[str]:
    found: list[str] = []
    child = component.get("child")
    if isinstance(child, str):
        found.append(child)
    children = component.get("children")
    if isinstance(children, list):
        found.extend(c for c in children if isinstance(c, str))
    elif isinstance(children, dict) and isinstance(children.get("componentId"), str):
        found.append(children["componentId"])
    tabs = component.get("tabs")
    if isinstance(tabs, list):
        found.extend(t["child"] for t in tabs if isinstance(t, dict) and isinstance(t.get("child"), str))
    for key in ("trigger", "content"):
        if isinstance(component.get(key), str):
            found.append(component[key])
    return found


def _graph_issues(surface: str, components: list[dict], report: Report) -> None:
    where = f"surface {surface!r}"
    ids: dict[str, dict] = {}
    for component in components:
        cid = component.get("id")
        if not isinstance(cid, str):
            continue
        if cid in ids:
            report.add("duplicate_id", where, f"id {cid!r} is used twice")
        ids[cid] = component
    if "root" not in ids:
        report.add("no_root", where, "no component has the id 'root'")
    for cid, component in ids.items():
        for child in _children_of(component):
            if child not in ids:
                report.add("missing_child", f"{where}/{cid}", f"child {child!r} does not exist")

    # Depth first, with the path so far: a loop would hang a renderer.
    done: set[str] = set()

    def walk(cid: str, path: tuple[str, ...]) -> None:
        if cid in path:
            report.add("cycle", where, " > ".join((*path, cid)))
            return
        if cid in done or cid not in ids:
            return
        for child in _children_of(ids[cid]):
            walk(child, (*path, cid))
        done.add(cid)

    if "root" in ids:
        walk("root", ())
    for cid in ids:
        if cid not in done:
            report.add("unreachable", f"{where}/{cid}", "nothing leads to this component")


def resolve_pointer(data: Any, path: str) -> tuple[bool, Any]:
    """JSON Pointer lookup. Returns (found, value)."""
    if path in ("", "/"):
        return True, data
    if not path.startswith("/"):
        return False, None
    node = data
    for raw in path[1:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
            node = node[int(key)]
        else:
            return False, None
    return True, node


def _walk_values(node: Any, trail: tuple = ()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_values(value, (*trail, key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk_values(value, (*trail, i))
    else:
        yield trail, node


def _bindings(node: Any, trail: tuple = ()):
    if isinstance(node, dict):
        if isinstance(node.get("@path"), str):
            yield trail, node["@path"]
        for key, value in node.items():
            yield from _bindings(value, (*trail, key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _bindings(value, (*trail, i))


def _template_ids(components: list[dict]) -> dict[str, str]:
    """Components stamped out by a List, mapped to the list path they read from."""
    by_id = {c.get("id"): c for c in components}
    templates: dict[str, str] = {}

    def mark(cid: str, list_path: str) -> None:
        if cid in templates or cid not in by_id:
            return
        templates[cid] = list_path
        for child in _children_of(by_id[cid]):
            mark(child, list_path)

    for component in components:
        children = component.get("children")
        if (
            isinstance(children, dict)
            and isinstance(children.get("path"), str)
            and isinstance(children.get("componentId"), str)
        ):
            mark(children["componentId"], children["path"])
    return templates


def _binding_issues(surface: str, components: list[dict], data: Any, report: Report) -> None:
    templates = _template_ids(components)
    for component in components:
        cid = str(component.get("id"))
        scope = templates.get(cid)
        for trail, path in _bindings(component):
            if path.startswith("/"):
                if not resolve_pointer(data, path)[0]:
                    report.add("unbound_path", f"surface {surface!r}/{cid}", f"{path} is not in the data model")
                continue
            # A relative path reads each row of its list.
            if scope is None:
                report.add("unbound_path", f"surface {surface!r}/{cid}", f"relative path {path} outside a list")
                continue
            found, rows = resolve_pointer(data, scope)
            if not found or not isinstance(rows, list) or not rows:
                report.add("unbound_path", f"surface {surface!r}/{cid}", f"list {scope} is not in the data model")
            elif not all(resolve_pointer(row, "/" + path)[0] for row in rows):
                report.add("unbound_path", f"surface {surface!r}/{cid}", f"{path} is missing from rows of {scope}")
        children = component.get("children")
        if isinstance(children, dict) and isinstance(children.get("path"), str):
            found, rows = resolve_pointer(data, children["path"])
            if not found or not isinstance(rows, list):
                report.add("unbound_path", f"surface {surface!r}/{cid}", f"{children['path']} is not a list in the data model")


def figures_in(text: str) -> list[str]:
    return _FIGURE.findall(text)


def literal_figures(components: list[dict]) -> list[tuple[str, str]]:
    """Every figure typed into a component, as (component id, figure)."""
    out = []
    for component in components:
        cid = str(component.get("id"))
        for trail, value in _walk_values(component):
            if trail and trail[0] in ("id", "component", "children", "child", "catalogId", "accessibility"):
                continue
            if trail and str(trail[-1]) in ("@path", "path", "url", "name"):
                continue
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                if trail and str(trail[-1]) in ("min", "max", "step", "value", "weight"):
                    continue
                out.append((cid, str(value)))
            elif isinstance(value, str):
                out.extend((cid, figure) for figure in figures_in(value))
    return out


def data_of(messages: list[Any], data: Any = None) -> dict:
    """The data model as it stands after the messages, merged over `data`."""
    model: dict = dict(data) if isinstance(data, dict) else {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        created = message.get("createSurface")
        if isinstance(created, dict) and isinstance(created.get("dataModel"), dict):
            model.update(created["dataModel"])
        update = message.get("updateDataModel")
        if isinstance(update, dict) and update.get("path") in (None, "/") and isinstance(update.get("value"), dict):
            model.update(update["value"])
    return model


def validate(
    messages: Any,
    *,
    catalog: dict | None = None,
    data: Any = None,
    check_bindings: bool = True,
    no_literal_figures: bool = False,
) -> Report:
    """Checks messages. Pass `data` when the app supplies the data model itself."""
    report = Report()
    if not isinstance(messages, list) or not messages:
        report.add("not_messages", "/", "expected a non-empty list of A2UI messages")
        return report
    try:
        catalog_json = json.dumps(catalog or basic_catalog(), sort_keys=True)
        for i, message in enumerate(messages):
            _schema_issues(message, i, catalog_json, report)
        model = data_of(messages, data)
        for surface, components in components_by_surface(messages).items():
            _graph_issues(surface, components, report)
            if check_bindings:
                _binding_issues(surface, components, model, report)
            if no_literal_figures:
                for cid, figure in literal_figures(components):
                    report.add("literal_figure", f"surface {surface!r}/{cid}", f"{figure} is typed in, not bound to data")
    except Exception as error:  # A validator bug must fail closed, never open.
        report.add("internal", "/", f"could not check this: {type(error).__name__}")
    return report
