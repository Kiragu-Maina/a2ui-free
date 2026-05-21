"""Test suite for a2ui_healer. Pure-Python, no LLM or ADK deps; run with:

    cd A2UI/samples/agent/adk/_shared && python test_a2ui_healer.py

Exits 0 on success; raises on first failing assertion.
"""
from __future__ import annotations
import json, sys

# Import directly (we are in _shared/, no sys.path mods needed).
from a2ui_healer import (
    heal_text,
    heal_a2ui_block,
    flatten_components,
    repair_envelope,
    is_a2ui_envelope,
)


# ─────────────────────────────────────────────────────────────────────────────
# helper
# ─────────────────────────────────────────────────────────────────────────────

def _eq(actual, expected, label):
    if actual != expected:
        print(f"\nFAIL [{label}]\n  expected: {expected}\n  actual:   {actual}")
        raise AssertionError(label)


PASSED: list[str] = []
def _pass(label):
    PASSED.append(label)
    print(f"PASS  {label}")


# ─────────────────────────────────────────────────────────────────────────────
# tests
# ─────────────────────────────────────────────────────────────────────────────

# 1. The user's reported failure: inline children defs at the top level.
bad = json.dumps([{
    "version": "v0.9",
    "updateComponents": {
        "surfaceId": "default",
        "components": [{
            "id": "card-content",
            "component": "Column",
            "children": [
                {"id": "image-container", "component": "Row", "children": ["image", "spacer"]},
                {"id": "spacer", "component": "Text", "text": {"value": " "}},
                {"id": "name", "component": "Text", "variant": "h3", "text": {"path": "name"}},
                {"id": "rating", "component": "Text", "text": {"path": "rating"}},
                {"id": "detail", "component": "Text", "text": {"path": "detail"}},
                {"id": "address", "component": "Text", "text": {"path": "address"}},
                {"id": "info-link", "component": "Text", "text": {"path": "infoLink"}},
                {"id": "book-button", "component": "Button", "child": "book-now",
                 "variant": "primary", "action": {"event": {"name": "book_restaurant",
                 "context": {"restaurantName": {"path": "name"}}}}},
            ],
        }],
    },
}])
got = json.loads(heal_a2ui_block(bad))
assert isinstance(got, list) and len(got) == 1, got
comps = got[0]["updateComponents"]["components"]
# card-content + its 8 inlined children = 9 components in the flat output.
_eq(len(comps), 9, "inline children: count")
card = next(c for c in comps if c["id"] == "card-content")
_eq(card["children"],
    ["image-container","spacer","name","rating","detail","address","info-link","book-button"],
    "inline children: refs are strings in original order")
ids = {c["id"] for c in comps}
assert ids == {"card-content","image-container","spacer","name","rating",
               "detail","address","info-link","book-button"}, ids
_pass("inline children -> flat siblings, refs as id-strings")


# 2. Inline `child` (singular)
bad = json.dumps([{
    "version": "v0.9",
    "updateComponents": {"surfaceId": "s", "components": [{
        "id": "btn", "component": "Button",
        "child": {"id": "btn-label", "component": "Text", "text": "Go"},
    }]},
}])
got = json.loads(heal_a2ui_block(bad))
comps = got[0]["updateComponents"]["components"]
btn = next(c for c in comps if c["id"] == "btn")
_eq(btn["child"], "btn-label", "singular child: id-string ref")
assert any(c["id"] == "btn-label" for c in comps)
_pass("inline child (singular) -> sibling, id-string ref")


# 3. Duplicate ids: first wins
bad = json.dumps([{
    "version": "v0.9",
    "updateComponents": {"surfaceId": "s", "components": [
        {"id": "x", "component": "Text", "text": "first"},
        {"id": "x", "component": "Text", "text": "second"},
        {"id": "y", "component": "Text", "text": "yval"},
    ]},
}])
got = json.loads(heal_a2ui_block(bad))
comps = got[0]["updateComponents"]["components"]
_eq(len(comps), 2, "duplicate ids: dedupe count")
x = next(c for c in comps if c["id"] == "x")
_eq(x["text"], "first", "duplicate ids: first wins")
_pass("duplicate ids -> first-seen kept, rest dropped")


# 4. Components missing required fields
bad = json.dumps([{
    "version": "v0.9",
    "updateComponents": {"surfaceId": "s", "components": [
        {"id": "a", "component": "Text", "text": "ok"},
        {"component": "Text", "text": "no id"},
        {"id": "c", "text": "no component"},
        {"id": "d", "component": "Text"},
    ]},
}])
got = json.loads(heal_a2ui_block(bad))
ids = [c["id"] for c in got[0]["updateComponents"]["components"]]
_eq(ids, ["a", "d"], "missing required fields: only valid kept")
_pass("missing id or component -> dropped")


# 5. List template form `children: {componentId, path}` preserved
bad = json.dumps([{
    "version": "v0.9",
    "updateComponents": {"surfaceId": "s", "components": [
        {"id": "lst", "component": "List", "direction": "vertical",
         "children": {"componentId": "tmpl", "path": "/items"}},
        {"id": "tmpl", "component": "Text", "text": {"path": "name"}},
    ]},
}])
got = json.loads(heal_a2ui_block(bad))
lst = next(c for c in got[0]["updateComponents"]["components"] if c["id"] == "lst")
_eq(lst["children"], {"componentId": "tmpl", "path": "/items"}, "List template form")
_pass("List template `children: {componentId, path}` preserved")


# 6. Multi-message envelope split
bad = json.dumps({
    "version": "v0.9",
    "createSurface": {"surfaceId": "s", "catalogId": "c"},
    "updateComponents": {"surfaceId": "s", "components": [{"id":"a","component":"Text","text":"hi"}]},
})
got = json.loads(heal_a2ui_block(bad))
assert isinstance(got, list) and len(got) == 2, got
assert any("createSurface" in e for e in got)
assert any("updateComponents" in e for e in got)
_pass("multi-message envelope -> split into separate envelopes")


# 7. Single envelope dict in -> dict out (renderer compatibility)
bad = json.dumps({
    "version": "v0.9",
    "updateComponents": {"surfaceId": "s", "components": [{"id":"a","component":"Text","text":"hi"}]},
})
got = json.loads(heal_a2ui_block(bad))
assert isinstance(got, dict), "single envelope: dict in -> dict out"
_pass("single envelope: dict in -> dict out")


# 8. Raw business objects interleaved
bad = json.dumps([
    {"name": "Joe", "rating": 4.5},
    {"version": "v0.9", "updateComponents": {"surfaceId": "s", "components": [{"id":"a","component":"Text","text":"hi"}]}},
    {"address": "..."},
])
got = json.loads(heal_a2ui_block(bad))
assert isinstance(got, list) and len(got) == 1
assert "updateComponents" in got[0]
_pass("raw business objects mixed in -> filtered out")


# 9. Deep nesting (3 levels of inline)
bad = json.dumps([{
    "version": "v0.9",
    "updateComponents": {"surfaceId": "s", "components": [{
        "id": "root", "component": "Column", "children": [
            {"id": "row", "component": "Row", "children": [
                {"id": "cell", "component": "Card", "child":
                    {"id": "inner-text", "component": "Text", "text": "leaf"}},
            ]},
        ],
    }]},
}])
got = json.loads(heal_a2ui_block(bad))
comps = got[0]["updateComponents"]["components"]
ids = sorted(c["id"] for c in comps)
_eq(ids, ["cell", "inner-text", "root", "row"], "deep nesting: all flattened")
root = next(c for c in comps if c["id"] == "root")
_eq(root["children"], ["row"], "deep nesting: root.children is id-string list")
cell = next(c for c in comps if c["id"] == "cell")
_eq(cell["child"], "inner-text", "deep nesting: cell.child is id-string")
_pass("deeply nested inline defs (3 levels) -> all flattened")


# 10. Self-reference cycle protection
bad = json.dumps([{
    "version": "v0.9",
    "updateComponents": {"surfaceId": "s", "components": [
        {"id": "a", "component": "Row", "children": ["a", "b"]},  # a references itself
        {"id": "b", "component": "Text", "text": "ok"},
    ]},
}])
got = json.loads(heal_a2ui_block(bad))
ids = sorted(c["id"] for c in got[0]["updateComponents"]["components"])
_eq(ids, ["a", "b"], "cycle: BFS dedupes, doesn't loop")
_pass("self-reference cycle -> handled, no infinite loop")


# 11. Non-JSON body returned unchanged
got = heal_a2ui_block("this is not JSON {")
_eq(got, "this is not JSON {", "non-JSON: unchanged")
_pass("non-JSON body -> returned unchanged")


# 12. All candidates non-envelope -> "[]"
got = heal_a2ui_block(json.dumps([{"name": "raw"}, {"x": 1}]))
_eq(got, "[]", "no envelopes: []")
_pass("all non-envelopes -> []")


# 13. Top-level heal_text -- the full pipeline incl. markdown fence rewrite
text_with_fence = (
    "Some preamble.\n"
    "```a2ui-json\n"
    + json.dumps([{
        "version": "v0.9",
        "updateComponents": {"surfaceId": "s", "components": [
            {"id": "root", "component": "Column", "children": [
                {"id": "title", "component": "Text", "text": "hi"},
            ]},
        ]},
    }]) + "\n```\n"
)
healed, notes = heal_text(text_with_fence)
assert "<a2ui-json>" in healed, "fence rewrite: <a2ui-json> tag present"
assert "```a2ui-json" not in healed, "fence rewrite: original fence gone"
assert len(notes) >= 2, f"notes recorded the fence rewrite and the heal: {notes}"
_pass("heal_text: ```a2ui-json fence -> <a2ui-json> tag + inner flatten + notes")


# 14. Generic ```json fence is rewritten ONLY if body looks A2UI-shaped.
text_a2ui_in_json_fence = (
    "Here is the UI:\n"
    "```json\n"
    + json.dumps([{
        "version": "v0.9",
        "createSurface": {"surfaceId": "default"},
    }]) + "\n```\n"
)
healed_a, notes_a = heal_text(text_a2ui_in_json_fence)
assert "<a2ui-json>" in healed_a
_pass("generic ```json fence with A2UI body -> rewritten")

text_python_in_json_fence = (
    "Here is some code:\n"
    "```json\n"
    + json.dumps({"foo": "bar"}) + "\n```\n"
)
healed_b, notes_b = heal_text(text_python_in_json_fence)
assert "```json" in healed_b, "non-A2UI ```json: must be preserved"
assert "<a2ui-json>" not in healed_b, "non-A2UI ```json: no tag injected"
_pass("generic ```json fence with NON-A2UI body -> left alone")


# 15. Idempotence: heal(heal(t)) == heal(t)
healed_once, _ = heal_text(text_with_fence)
healed_twice, _ = heal_text(healed_once)
_eq(healed_twice, healed_once, "idempotence")
_pass("idempotence: heal(heal(t)) == heal(t)")


# 16. Empty input
healed_e, notes_e = heal_text("")
_eq(healed_e, "", "empty input")
_eq(notes_e, [], "empty input notes")
_pass("empty input -> empty output, no notes")


# 17. Plain text with no a2ui markers
plain = "Plain prose, no A2UI in sight."
healed_p, notes_p = heal_text(plain)
_eq(healed_p, plain, "plain text untouched")
_pass("plain text -> unchanged")


# 18. is_a2ui_envelope unit
assert is_a2ui_envelope({"version": "v0.9", "createSurface": {}})
assert not is_a2ui_envelope({"version": "v0.9"})              # no message type
assert not is_a2ui_envelope({"createSurface": {}})             # no version
assert not is_a2ui_envelope([1, 2, 3])
_pass("is_a2ui_envelope: shape check")


print(f"\nall {len(PASSED)} tests passed")
sys.exit(0)
