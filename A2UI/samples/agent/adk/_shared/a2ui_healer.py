# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Best-effort self-healing for A2UI v0.9 responses.

Free-tier council members produce malformed A2UI in a predictable set of
shapes. Rather than burn another full council pass when the validator
rejects, this module attempts to repair the response in-place. Repairs
are conservative -- if a repair cannot be applied confidently the text
is returned unchanged for the validator to reject normally.

Two callers:
  - council_llm.py: the post-council, pre-yield normalization step that
    runs on every successful member / judge response.
  - restaurant_finder/agent.py: the validation-retry loop, on a validation
    failure -- runs heal_text() before deciding to retry with an LLM,
    which often turns the rejection into an acceptance without a second
    round-trip.

The repair set:

  1. Markdown fences (```a2ui-json / ```json / bare ```) -> <a2ui-json>
     tags. Generic ``` fences are only rewritten when the body matches
     the A2UI envelope shape (avoid hijacking unrelated code samples).
  2. Inline component definitions inside `children: [...]` -> extracted
     to flat siblings, slot replaced with string id reference. This is
     the most common cause of "'children', 'component' were unexpected"
     validator errors.
  3. Inline component definition in `child:` (singular) -> same.
  4. Inline component definition in `children: {...}` dict form (when
     it is NOT the legitimate List template {componentId, path}).
  5. Duplicate component ids -> first-seen wins, duplicates dropped.
  6. Components missing required `id` or `component` -> dropped.
  7. Multi-message envelopes ({version, createSurface, updateComponents}
     in one wrapper) -> split into separate envelopes.
  8. Non-envelope objects mixed with envelopes (raw business objects
     leaking from candidates) -> filtered out.
  9. Top-level dict vs list -> normalized.

Returns NOT just the repaired text but also a list of human-readable
notes describing what was fixed -- useful for telemetry, debugging, and
deciding whether a heal pass changed enough to warrant re-validation.
"""

from __future__ import annotations

import copy
import json
import logging
import re

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Patterns and constants
# ─────────────────────────────────────────────────────────────────────────────

# Explicit A2UI fence: ```a2ui-json (or a2uijson / a2ui_json / a2ui-anything)
_A2UI_FENCE_RE = re.compile(
    r"```\s*a2ui[a-z_-]*\s*\n(.*?)\n```",
    re.IGNORECASE | re.DOTALL,
)
# Generic ```json or bare ``` fence -- only rewritten if body looks A2UI-shaped.
_GENERIC_FENCE_RE = re.compile(
    r"```\s*(?:json)?\s*\n(.*?)\n```",
    re.IGNORECASE | re.DOTALL,
)
# An A2UI v0.9 envelope contains "version" and one of the known message types.
_A2UI_SHAPE_RE = re.compile(
    r'"version"\s*:\s*"v0\.[0-9]+"[\s\S]*'
    r'"(createSurface|updateComponents|updateDataModel|deleteSurface|appendComponents|removeComponents|patchComponent|navigate|invokeMethod|setSurfaceState|toast|dismissToast)"',
    re.IGNORECASE,
)

A2UI_MESSAGE_TYPES = {
    "createSurface", "updateComponents", "updateDataModel", "deleteSurface",
    "appendComponents", "removeComponents", "patchComponent", "navigate",
    "invokeMethod", "setSurfaceState", "toast", "dismissToast",
}

# Already-tagged A2UI block; used to re-enter and filter its contents.
_A2UI_TAG_RE = re.compile(
    r"<a2ui-json>\s*(.*?)\s*</a2ui-json>", re.DOTALL | re.IGNORECASE
)


# ─────────────────────────────────────────────────────────────────────────────
# Envelope shape checks
# ─────────────────────────────────────────────────────────────────────────────

def is_a2ui_envelope(obj) -> bool:
    """True iff obj is a v0.9 envelope (has `version` + at least one message type)."""
    return (
        isinstance(obj, dict)
        and "version" in obj
        and any(k in A2UI_MESSAGE_TYPES for k in obj.keys())
    )


# ─────────────────────────────────────────────────────────────────────────────
# Component-tree repair
# ─────────────────────────────────────────────────────────────────────────────

def flatten_components(components: list, notes: list[str] | None = None) -> list:
    """Repair a flat components array for updateComponents / appendComponents.

    Pulls inline child / children definitions out into sibling entries with
    string-id refs, dedupes by id (first wins), drops entries without `id`
    or `component`. Recursive via BFS so deeply-nested inlines flatten too.

    Returns a NEW list; does not mutate input.
    """
    if not isinstance(components, list):
        return components

    flat: dict[str, dict] = {}
    order: list[str] = []
    queue: list = [copy.deepcopy(c) for c in components]
    dropped_no_id = 0
    dropped_no_type = 0
    dropped_duplicate = 0
    inlined_children = 0
    inlined_child = 0

    while queue:
        c = queue.pop(0)
        if not isinstance(c, dict):
            continue
        cid = c.get("id")
        ctype = c.get("component")
        if not cid:
            dropped_no_id += 1
            continue
        if not ctype:
            dropped_no_type += 1
            continue
        if cid in flat:
            dropped_duplicate += 1
            continue

        # Repair `children: [...]`
        children = c.get("children")
        if isinstance(children, list):
            new_children: list[str] = []
            for ch in children:
                if isinstance(ch, str):
                    new_children.append(ch)
                elif isinstance(ch, dict):
                    ch_id = ch.get("id")
                    if not ch_id:
                        continue  # unreferencable, drop
                    new_children.append(ch_id)
                    if ch_id not in flat:
                        queue.append(ch)
                        inlined_children += 1
                # ints / None / etc. silently dropped
            c["children"] = new_children
        elif isinstance(children, dict):
            # List template form: {componentId, path}. Legit -- preserve.
            if "componentId" in children:
                pass
            # Single inline def in dict form (LLM mistake) -- lift it out.
            elif children.get("id") and children.get("component"):
                ch_id = children["id"]
                c["children"] = [ch_id]
                if ch_id not in flat:
                    queue.append(children)
                    inlined_children += 1
            # else: unknown dict; leave alone rather than corrupt unfamiliar shapes.

        # Repair `child` (singular, used by Card, Button, etc.)
        child = c.get("child")
        if isinstance(child, dict):
            ch_id = child.get("id")
            if ch_id:
                c["child"] = ch_id
                if ch_id not in flat:
                    queue.append(child)
                    inlined_child += 1
            else:
                c.pop("child", None)  # strip unreferencable inline def

        flat[cid] = c
        order.append(cid)

    if notes is not None:
        if inlined_children:
            notes.append(f"flattened {inlined_children} inline child definition(s) inside children")
        if inlined_child:
            notes.append(f"flattened {inlined_child} inline child definition(s) inside child")
        if dropped_duplicate:
            notes.append(f"deduped {dropped_duplicate} duplicate component id(s)")
        if dropped_no_id:
            notes.append(f"dropped {dropped_no_id} component(s) without id")
        if dropped_no_type:
            notes.append(f"dropped {dropped_no_type} component(s) without `component` type")

    return [flat[cid] for cid in order]


def repair_envelope(env: dict, notes: list[str] | None = None) -> list[dict]:
    """Repair one envelope; returns a LIST because multi-message envelopes split."""
    if not isinstance(env, dict):
        return []

    types_present = [k for k in env if k in A2UI_MESSAGE_TYPES]
    if not types_present:
        return []
    version = env.get("version", "v0.9")
    if len(types_present) > 1:
        if notes is not None:
            notes.append(
                f"split multi-message envelope into {len(types_present)} envelopes "
                f"({', '.join(types_present)})"
            )
        out: list[dict] = []
        for mt in types_present:
            split_env = {"version": version, mt: env[mt]}
            out.extend(repair_envelope(split_env, notes))
        return out

    op = types_present[0]
    payload = env.get(op)
    if isinstance(payload, dict):
        comps = payload.get("components")
        if isinstance(comps, list):
            payload["components"] = flatten_components(comps, notes)
        # patchComponent uses singular "component" key
        if op == "patchComponent":
            comp = payload.get("component")
            if isinstance(comp, dict):
                flat = flatten_components([comp], notes)
                if flat:
                    payload["component"] = flat[0]
    return [env]


# ─────────────────────────────────────────────────────────────────────────────
# Block-level (inside <a2ui-json> tags) repair
# ─────────────────────────────────────────────────────────────────────────────

def heal_a2ui_block(body: str, notes: list[str] | None = None) -> str:
    """Filter non-envelopes + repair envelopes inside one <a2ui-json> body."""
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return body  # let the downstream validator complain

    if isinstance(parsed, dict):
        candidates = [parsed]
        was_dict = True
    elif isinstance(parsed, list):
        candidates = parsed
        was_dict = False
    else:
        return body

    envelopes = [c for c in candidates if is_a2ui_envelope(c)]
    dropped = len(candidates) - len(envelopes)
    if dropped and notes is not None:
        notes.append(f"dropped {dropped} non-envelope object(s)")

    if not envelopes:
        if was_dict and notes is not None:
            notes.append("top-level dict was not an envelope; replaced with []")
        return "[]"

    repaired: list[dict] = []
    for env in envelopes:
        repaired.extend(repair_envelope(env, notes))

    if was_dict and len(repaired) == 1:
        return json.dumps(repaired[0], indent=2)
    return json.dumps(repaired, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Text-level (markdown fences + tag re-entry)
# ─────────────────────────────────────────────────────────────────────────────

def normalize_a2ui_format(text: str, notes: list[str] | None = None) -> str:
    """Rewrite markdown fences to <a2ui-json> tags, then re-enter tags to
    repair their contents."""
    if not text:
        return text

    n_a2ui_fence = 0
    n_generic_fence = 0

    def _rewrite_a2ui(m):
        nonlocal n_a2ui_fence
        n_a2ui_fence += 1
        return f"<a2ui-json>\n{m.group(1).strip()}\n</a2ui-json>"

    text = _A2UI_FENCE_RE.sub(_rewrite_a2ui, text)

    def _maybe_generic(m):
        nonlocal n_generic_fence
        body = m.group(1).strip()
        if _A2UI_SHAPE_RE.search(body):
            n_generic_fence += 1
            return f"<a2ui-json>\n{body}\n</a2ui-json>"
        return m.group(0)

    text = _GENERIC_FENCE_RE.sub(_maybe_generic, text)
    text = _A2UI_TAG_RE.sub(
        lambda m: f"<a2ui-json>\n{heal_a2ui_block(m.group(1), notes)}\n</a2ui-json>",
        text,
    )

    if notes is not None:
        if n_a2ui_fence:
            notes.append(f"rewrote {n_a2ui_fence} ```a2ui-* fence(s) to <a2ui-json> tag(s)")
        if n_generic_fence:
            notes.append(f"rewrote {n_generic_fence} ```json/```-fenced A2UI-shaped block(s)")

    return text


# ─────────────────────────────────────────────────────────────────────────────
# Top-level entry point
# ─────────────────────────────────────────────────────────────────────────────

def heal_text(text: str) -> tuple[str, list[str]]:
    """Top-level entry. Run every repair pass; return (healed, notes).

    `notes` is empty when nothing changed. The caller (agent.py validation
    loop) uses notes to decide whether to re-validate before re-prompting.

    Idempotent: heal_text(heal_text(t)[0])[0] == heal_text(t)[0].
    """
    if not text:
        return text, []
    notes: list[str] = []
    healed = normalize_a2ui_format(text, notes)
    return healed, notes
