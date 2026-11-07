import json
import random
from pathlib import Path

import pytest

from a2ui_free import Chain, LlmError, chain, connect, generate, repair, validate, with_data
from a2ui_free.bench import TASKS, coverage, explain_figure, figure_errors, one_run, source_figures, summarise
from a2ui_free.blueprint import expand, generate_from_plan, read_plan
from a2ui_free.generate import data_shape, system_prompt
from a2ui_free.llm import FunctionLlm
from a2ui_free.validate import BASIC_CATALOG_ID, figures_in, resolve_pointer

CATALOG = BASIC_CATALOG_ID


def surface(components, **extra):
    return [
        {
            "version": "1.0",
            "createSurface": {"surfaceId": "m", "catalogId": CATALOG, "components": components, **extra},
        }
    ]


def text(cid, value):
    return {"id": cid, "component": "Text", "text": value}


GOOD = surface(
    [
        {"id": "root", "component": "Column", "children": ["title", "rows"]},
        text("title", "Lines"),
        {"id": "rows", "component": "List", "children": {"componentId": "row", "path": "/lines"}},
        text("row", {"@path": "item"}),
    ],
    dataModel={"lines": [{"item": "Nails"}]},
)


class TestValidate:
    def test_a_good_surface_passes(self):
        assert validate(GOOD).ok

    @pytest.mark.parametrize("junk", [None, 5, "x", [], {}, [1], [None], [{}], [[]]])
    def test_junk_is_refused_without_raising(self, junk):
        assert not validate(junk).ok

    def test_unknown_component(self):
        report = validate(surface([{"id": "root", "component": "Hologram"}]))
        assert "schema" in report.codes()

    def test_errors_are_written_for_a_model_to_act_on(self):
        unknown = str(validate(surface([{"id": "root", "component": "Hologram"}])))
        assert "Hologram is not a component in this catalog" in unknown
        missing = str(validate(surface([{"id": "root", "component": "Text"}])))
        assert "Text: 'text' is a required property" in missing

    def test_version_must_be_current(self):
        bad = json.loads(json.dumps(GOOD))
        bad[0]["version"] = "v0.9"
        assert "schema" in validate(bad).codes()

    def test_theme_is_forbidden_in_v1(self):
        bad = surface([text("root", "Hi")], theme={"primaryColor": "#000000"})
        assert not validate(bad).ok

    def test_a_missing_child(self):
        report = validate(surface([{"id": "root", "component": "Column", "children": ["ghost"]}]))
        assert "missing_child" in report.codes()

    def test_no_root(self):
        assert "no_root" in validate(surface([text("a", "Hi")])).codes()

    def test_duplicate_ids(self):
        report = validate(
            surface([{"id": "root", "component": "Column", "children": ["a"]}, text("a", "x"), text("a", "y")])
        )
        assert "duplicate_id" in report.codes()

    def test_a_cycle_ends_instead_of_hanging(self):
        report = validate(
            surface(
                [
                    {"id": "root", "component": "Column", "children": ["a"]},
                    {"id": "a", "component": "Column", "children": ["root"]},
                ]
            )
        )
        assert "cycle" in report.codes()

    def test_unreachable(self):
        report = validate(surface([text("root", "Hi"), text("lost", "Bye")]))
        assert "unreachable" in report.codes()

    def test_a_binding_must_point_at_data(self):
        report = validate(surface([text("root", {"@path": "/nope"})], dataModel={"yes": 1}))
        assert "unbound_path" in report.codes()

    def test_a_relative_path_outside_a_list_is_refused(self):
        report = validate(surface([text("root", {"@path": "name"})]))
        assert "unbound_path" in report.codes()

    def test_a_row_field_must_exist_in_every_row(self):
        bad = surface(
            [
                {"id": "root", "component": "List", "children": {"componentId": "row", "path": "/a"}},
                text("row", {"@path": "name"}),
            ],
            dataModel={"a": [{"name": "x"}, {"other": 1}]},
        )
        assert "unbound_path" in validate(bad).codes()

    def test_data_can_come_from_the_app(self):
        ui = surface([text("root", {"@path": "/total"})])
        assert validate(ui, data={"total": 5}).ok
        assert not validate(ui, data={"other": 5}).ok

    def test_typed_figures_are_refused_when_asked(self):
        ui = surface([text("root", "Total KES 12,400")])
        assert validate(ui).ok
        assert "literal_figure" in validate(ui, no_literal_figures=True).codes()

    def test_a_bound_figure_passes(self):
        ui = surface([text("root", {"@path": "/total"})])
        assert validate(ui, data={"total": 12400}, no_literal_figures=True).ok

    def test_short_labels_are_not_figures(self):
        ui = surface([text("root", "Step 1 of the plan")])
        assert validate(ui, no_literal_figures=True).ok

    @pytest.mark.parametrize(
        "line,expected",
        [
            ("KES 1,200.50 today", ["1,200.50"]),
            ("up 40% this week", ["40%"]),
            ("3.5 stars", ["3.5"]),
            ("147 orders", ["147"]),
            ("Step 1", []),
            ("v2 release", []),
        ],
    )
    def test_figures_in(self, line, expected):
        assert figures_in(line) == expected

    def test_pointers(self):
        data = {"a": [{"b": 1}], "x/y": 2, "m~n": 3}
        assert resolve_pointer(data, "/a/0/b") == (True, 1)
        assert resolve_pointer(data, "/x~1y") == (True, 2)
        assert resolve_pointer(data, "/m~0n") == (True, 3)
        assert resolve_pointer(data, "/a/5")[0] is False
        assert resolve_pointer(data, "nope")[0] is False


class TestRepair:
    def test_prose_and_a_fence(self):
        fixed = repair("Sure!\n```json\n" + json.dumps(GOOD) + "\n```\nEnjoy.")
        assert validate(fixed.messages).ok

    def test_visible_reasoning(self):
        fixed = repair("<think>let me see [1,2]</think>" + json.dumps(GOOD))
        assert validate(fixed.messages).ok

    def test_trailing_commas(self):
        fixed = repair(json.dumps(GOOD).replace('"Lines"', '"Lines",').replace(',"text":', ',"text":', 1)[:-2] + ",]}"[0:0] + "}]")
        assert isinstance(fixed.messages, list)
        assert repair('[{"createSurface": {"components": [{"id":"root","component":"Text","text":"Hi",},],}},]').messages

    def test_nested_children_are_lifted(self):
        fixed = repair(
            surface(
                [
                    {
                        "id": "root",
                        "component": "Column",
                        "children": [{"id": "a", "component": "Text", "text": "Hi"}, {"component": "Text", "text": "Yo"}],
                    }
                ]
            )
        )
        assert validate(fixed.messages).ok
        assert len(fixed.messages[0]["createSurface"]["components"]) == 3

    def test_old_v08_shape(self):
        fixed = repair(surface([{"id": "root", "component": {"Text": {"text": {"literalString": "Hi"}}}}]))
        assert validate(fixed.messages).ok

    def test_value_wrappers_and_plain_path(self):
        fixed = repair(surface([text("root", {"path": "/x"})], dataModel={"x": 1}))
        assert validate(fixed.messages).ok

    def test_two_step_surface_is_joined(self):
        fixed = repair(
            [
                {"version": "v0.9", "createSurface": {"surfaceId": "m"}},
                {"version": "v0.9", "updateComponents": {"surfaceId": "m", "components": [text("root", "Hi")]}},
            ]
        )
        assert len(fixed.messages) == 1
        assert validate(fixed.messages).ok

    def test_a_message_doing_two_jobs_is_split(self):
        fixed = repair(
            [{"version": "1.0", "createSurface": {"surfaceId": "m"}, "updateComponents": {"surfaceId": "m", "components": [text("root", "Hi")]}}]
        )
        assert validate(fixed.messages).ok

    def test_bare_component_list(self):
        assert validate(repair([text("root", "Hi")]).messages).ok

    def test_a_lone_top_component_becomes_the_root(self):
        fixed = repair(surface([{"id": "page", "component": "Column", "children": ["a"]}, text("a", "Hi")]))
        assert validate(fixed.messages).ok

    def test_theme_is_removed(self):
        assert validate(repair(surface([text("root", "Hi")], theme={"a": 1})).messages).ok

    def test_repair_never_changes_what_a_value_says(self):
        fixed = repair(surface([text("root", "Total KES 999")]))
        assert fixed.messages[0]["createSurface"]["components"][0]["text"] == "Total KES 999"

    def test_repair_settles(self):
        once = repair("```json\n" + json.dumps(GOOD) + "\n```")
        twice = repair(once.messages)
        assert once.messages == twice.messages
        assert twice.notes == []

    @pytest.mark.parametrize("junk", ["", "no json here", "{", "[", "null", "42", '{"a":1}', "```", None, 5])
    def test_junk_never_raises(self, junk):
        result = repair(junk)
        assert not validate(result.messages).ok


class TestFuzz:
    def mutate(self, node, r):
        junk = [None, True, -1, 3.5, "", "root", [], {}, {"@path": 5}, ["root"], {"component": 1}, "x" * 500]
        if isinstance(node, dict) and node:
            key = r.choice(list(node))
            if r.random() < 0.3:
                node[key] = r.choice(junk)
            elif r.random() < 0.2:
                del node[key]
            else:
                self.mutate(node[key], r)
        elif isinstance(node, list) and node:
            i = r.randrange(len(node))
            if r.random() < 0.3:
                node[i] = r.choice(junk)
            elif r.random() < 0.2:
                del node[i]
            else:
                self.mutate(node[i], r)

    def test_ten_thousand_mangled_surfaces_never_raise(self):
        r = random.Random(11)
        passed = 0
        for _ in range(10_000):
            messages = json.loads(json.dumps(GOOD))
            for _ in range(r.randint(1, 4)):
                self.mutate(messages, r)
            first = validate(messages)
            second = validate(repair(messages).messages)
            assert "internal" not in first.codes(), messages
            assert "internal" not in second.codes(), messages
            passed += second.ok
        assert 0 < passed < 10_000


class TestLlm:
    def test_names(self):
        assert connect("ollama/llama3.2:3b").base_url.startswith("http://localhost:11434")
        assert connect("ollama/llama3.2:3b").model == "llama3.2:3b"
        custom = connect("openai-compatible:https://my.host/v1|mine")
        assert (custom.base_url, custom.model) == ("https://my.host/v1", "mine")

    def test_a_missing_key_says_which(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        with pytest.raises(LlmError, match="GROQ_API_KEY"):
            connect("groq/some-model")

    @pytest.mark.parametrize("bad", ["nope", "madeup/model", "openai-compatible:only"])
    def test_bad_names(self, bad):
        with pytest.raises(LlmError):
            connect(bad)

    def test_a_chain_moves_on_and_rests_what_failed(self):
        calls = []

        def broken(system, user):
            calls.append("a")
            raise LlmError("down", cool_off=60)

        clock = [0.0]
        models = Chain([FunctionLlm(broken, "a"), FunctionLlm(lambda s, u: "ok", "b")], clock=lambda: clock[0])
        assert models.complete("s", "u") == "ok"
        assert models.complete("s", "u") == "ok"
        assert calls == ["a"]
        clock[0] = 100
        models.complete("s", "u")
        assert calls == ["a", "a"]

    def test_a_crashing_function_does_not_end_the_chain(self):
        def crash(system, user):
            raise RuntimeError("boom")

        assert Chain([FunctionLlm(crash, "a"), FunctionLlm(lambda s, u: "ok", "b")]).complete("s", "u") == "ok"

    def test_all_failing_says_so(self):
        def down(system, user):
            raise LlmError("down")

        with pytest.raises(LlmError, match="every model failed"):
            chain(down).complete("s", "u")

    def test_any_function_is_a_model(self):
        assert chain(lambda s, u: "hi").complete("s", "u") == "hi"


class TestGenerate:
    DATA = {"total": 12400, "name": "Wanjiru"}

    def reply(self, *components):
        return lambda system, user: json.dumps(surface(list(components)))

    def test_the_model_never_sees_a_value_when_bound(self):
        seen = {}

        def model(system, user):
            seen["prompt"] = system + user
            return json.dumps(surface([text("root", {"@path": "/total"})]))

        result = generate("show it", model, data=self.DATA)
        assert result.ok
        assert "/total" in seen["prompt"]
        assert "12400" not in seen["prompt"]
        assert "Wanjiru" not in seen["prompt"]

    def test_the_real_data_goes_in_afterwards(self):
        result = generate("show it", self.reply(text("root", {"@path": "/total"})), data=self.DATA)
        assert result.messages[0]["createSurface"]["dataModel"] == self.DATA

    def test_a_refusal_is_sent_back_and_fixed(self):
        replies = iter(
            [
                json.dumps(surface([text("root", "Total 12,400")])),
                json.dumps(surface([text("root", {"@path": "/total"})])),
            ]
        )
        prompts = []

        def model(system, user):
            prompts.append(user)
            return next(replies)

        result = generate("show it", model, data=self.DATA)
        assert result.ok
        assert len(result.attempts) == 2
        assert "literal_figure" in prompts[1]

    def test_it_gives_up_with_nothing_rather_than_ship_bad_ui(self):
        result = generate("show it", lambda s, u: "I cannot do that.", data=self.DATA, attempts=2)
        assert not result.ok
        assert result.messages == []
        assert len(result.attempts) == 2

    def test_a_dead_provider_is_a_result_not_a_crash(self):
        def down(system, user):
            raise LlmError("down")

        result = generate("show it", down)
        assert not result.ok
        assert "every model failed" in result.error

    def test_the_prompt_is_written_from_the_catalog(self):
        prompt = system_prompt(data=None, bound=True)
        assert "- Text:" in prompt
        assert "- Slider:" in prompt

    def test_data_shape_has_no_values(self):
        lines = "\n".join(data_shape(TASKS["invoice"][1]))
        assert "/lines: list of rows" in lines
        assert "15052" not in lines
        assert "Wanjiru" not in lines

    def test_with_data_leaves_the_rest_alone(self):
        out = with_data(surface([text("root", "Hi")]), {"a": 1})
        assert out[0]["createSurface"]["dataModel"] == {"a": 1}


class TestBench:
    def test_source_figures_ignore_formatting(self):
        truth = source_figures({"a": 1200.5, "b": "INV-20418", "c": [{"d": 40}]})
        assert {"1200.5", "20418", "40"} <= truth

    def test_a_copied_figure_is_right_and_a_changed_one_is_wrong(self):
        data = {"total": 15052.65}
        assert figure_errors(surface([text("root", "Total 15,052.65")]), data) == (1, 0)
        assert figure_errors(surface([text("root", "Total 15,502.65")]), data) == (1, 1)

    def test_bound_figures_are_counted_as_shown_and_right(self):
        data = {"total": 15052.65}
        assert figure_errors(surface([text("root", {"@path": "/total"})]), data) == (1, 0)

    def test_a_run_and_its_summary(self, tmp_path):
        def model(system, user):
            return json.dumps(surface([text("root", {"@path": "/total"})]))

        run = one_run("fake", "invoice", "bound", 0, llm=model)
        assert run.final_valid and run.raw_valid and run.figures_wrong == 0
        path: Path = tmp_path / "r.jsonl"
        path.write_text(json.dumps(run.__dict__) + "\n")
        assert summarise(path)["fake | bound"]["after_retry"] == 1


class TestBlueprint:
    DATA = TASKS["invoice"][1]
    PLAN = {
        "title": "Invoice",
        "blocks": [
            {"heading": "Who it is for"},
            {"fields": [{"label": "Customer", "path": "/customer"}, {"label": "Total due", "path": "/total"}]},
            {"table": {"path": "/lines", "columns": [{"label": "Item", "field": "item"}, {"label": "Qty", "field": "qty"}]}},
        ],
    }

    def test_a_plan_expands_to_valid_a2ui_that_shows_the_data(self):
        plan, report = read_plan(self.PLAN, self.DATA)
        assert report.ok
        messages = expand(plan)
        assert validate(messages, data=self.DATA, no_literal_figures=True).ok
        shown = figure_errors(messages, self.DATA)
        assert shown[0] > 0 and shown[1] == 0

    @pytest.mark.parametrize(
        "bad",
        [
            None,
            5,
            [],
            {},
            {"title": "x"},
            {"title": "x", "blocks": []},
            {"title": "x", "blocks": [{"chart": {}}]},
            {"title": "x", "blocks": [{"fields": [{"label": "a", "path": "/nope"}]}]},
            {"title": "x", "blocks": [{"fields": [{"label": "a", "path": "/lines"}]}]},
            {"title": "x", "blocks": [{"fields": [{"label": "Total 500", "path": "/total"}]}]},
            {"title": "x", "blocks": [{"table": {"path": "/total", "columns": [{"label": "a", "field": "b"}]}}]},
            {"title": "x", "blocks": [{"table": {"path": "/lines", "columns": [{"label": "a", "field": "nope"}]}}]},
        ],
    )
    def test_bad_plans_are_refused_without_raising(self, bad):
        plan, report = read_plan(bad, self.DATA)
        assert plan is None or not report.ok

    def test_a_figure_in_a_heading_is_refused(self):
        _, report = read_plan({"title": "x", "blocks": [{"heading": "Spent 15,052.65"}]}, self.DATA)
        assert "figure" in str(report)

    def test_the_model_never_sees_a_value(self):
        seen = {}

        def model(system, user):
            seen["all"] = system + user
            return json.dumps(self.PLAN)

        result = generate_from_plan("show it", model, self.DATA)
        assert result.ok
        assert "15052" not in seen["all"] and "Wanjiru" not in seen["all"]
        assert result.messages[0]["createSurface"]["dataModel"] == self.DATA

    def test_a_refused_plan_is_sent_back(self):
        replies = iter(['{"title": "x", "blocks": [{"chart": 1}]}', json.dumps(self.PLAN)])
        result = generate_from_plan("show it", lambda s, u: next(replies), self.DATA)
        assert result.ok and len(result.attempts) == 2

    def test_it_gives_up_with_nothing(self):
        result = generate_from_plan("show it", lambda s, u: "no", self.DATA, attempts=2)
        assert not result.ok and result.messages == []

    def test_any_plan_read_expands_to_valid_a2ui(self):
        r = random.Random(3)
        fields = [{"label": "Value", "path": p} for p in ("/customer", "/number", "/tax", "/total")]
        columns = [{"label": "Col", "field": f} for f in ("item", "qty", "amount")]
        for _ in range(500):
            blocks = [{"fields": r.sample(fields, 1)}]  # a plan of headings alone is refused
            for _ in range(r.randint(0, 5)):
                kind = r.choice(("heading", "fields", "table"))
                if kind == "heading":
                    blocks.append({"heading": "Words"})
                elif kind == "fields":
                    blocks.append({"fields": r.sample(fields, r.randint(1, 4))})
                else:
                    blocks.append({"table": {"path": "/lines", "columns": r.sample(columns, r.randint(1, 3))}})
            plan, report = read_plan({"title": "T", "blocks": blocks}, self.DATA)
            assert report.ok
            assert validate(expand(plan), data=self.DATA, no_literal_figures=True).ok


class TestCoverage:
    def test_a_surface_can_be_accurate_by_showing_nothing(self):
        data = {"total": 15052.65, "tax": 2076.4}
        empty = surface([text("root", "Your invoice")])
        assert figure_errors(empty, data) == (0, 0)
        assert coverage(empty, data) == 0

    def test_coverage_counts_what_reaches_the_screen(self):
        data = {"total": 15052.65, "tax": 2076.4}
        assert coverage(surface([text("root", {"@path": "/total"})]), data) == 0.5


# Found by reading benchmark replies: 29 of 258 blocks held more than one part, and
# everything beside a heading vanished, which left valid screens with no data.
PLAN_DATA = {"total": 12, "rows": [{"name": "a"}]}


def test_a_heading_beside_fields_and_a_table_keeps_all_three():
    plan, report = read_plan(
        {
            "title": "Shop",
            "blocks": [
                {
                    "heading": "Today",
                    "fields": [{"label": "Total", "path": "/total"}],
                    "table": {"path": "/rows", "columns": [{"label": "Name", "field": "name"}]},
                }
            ],
        },
        PLAN_DATA,
    )
    assert report.ok, str(report)
    assert [next(iter(block)) for block in plan.blocks] == ["heading", "fields", "table"]


def test_a_field_with_no_path_beside_a_heading_is_refused():
    _, report = read_plan(
        {"title": "Shop", "blocks": [{"heading": "Today", "fields": [{"label": "Total"}]}]},
        PLAN_DATA,
    )
    assert not report.ok


def test_a_plan_of_headings_alone_is_refused():
    plan, report = read_plan({"title": "Shop", "blocks": [{"heading": "Today"}]}, PLAN_DATA)
    assert plan is None
    assert not report.ok


def test_a_correct_gap_is_derived_and_a_slipped_one_is_not():
    data = {"budget": 48250, "spent": 51377}
    assert explain_figure("3127", data) == "derived"
    assert explain_figure("3137", data) == "unsupported"
