# a2ui-free

Generate [A2UI](https://github.com/a2ui-project/a2ui) v1.0 screens with any language model, and only ship screens that passed.

Open source (MIT), one dependency, and no provider lock-in. Point it at Ollama on a laptop, a hosted API, or your own function.

## What it found

Four small local models, six tasks, 144 runs across three ways of asking. A run counts as useful if the screen passed every check and showed at least half of the figures in the data.

| How the model was asked | Passed the checks | Useful | Figures not in the data, on passing screens | Median seconds |
| --- | --- | --- | --- | --- |
| **Inline:** sees the values, writes A2UI | 23 of 48 | 17 of 48 | 4 of 186 | 69 |
| **Bound:** sees paths, writes A2UI | 8 of 48 | 3 of 48 | 0 of 23 | 53 |
| **Blueprint:** sees paths, writes a small plan, code writes A2UI | 40 of 48 | 39 of 48 | 0 of 336 | 10 |

Blueprint was run again after a bug fix, so read [the experiment notes](docs/EXPERIMENT.md) before quoting this. They hold the method, the limits and the false labels this table does not show. Blueprint's zero is guaranteed by construction, so it says nothing about the model's honesty. All raw replies are in `benchmarks/`.

## Install

```bash
pip install git+https://github.com/Kiragu-Maina/a2ui-free
```

It needs Python 3.10 or later. It is not on PyPI yet.

## Use it

Let the model plan and let code build. This is the mode to start with.

```bash
a2ui-free generate "Show this invoice" --plan --model ollama/llama3.2:3b --data invoice.json
```

From Python:

```python
from a2ui_free import generate_from_plan

data = {"customer": "Wanjiru Hardware", "total": 15052.65}
result = generate_from_plan("Show this invoice", "ollama/llama3.2:3b", data)

if result.ok:
    send_to_client(result.messages)   # A2UI v1.0 messages, with the data attached
else:
    print(result.report)              # why it was refused, nothing to render
```

The model sees the paths and never the values. It answers with a plan like this:

```json
{"title": "Invoice",
 "blocks": [
   {"fields": [{"label": "Customer", "path": "/customer"}]},
   {"table": {"path": "/lines", "columns": [{"label": "Item", "field": "item"}]}}]}
```

`read_plan` checks it against your data and never raises. A path that does not exist or a label with a figure in it is refused and sent back to the model. `expand` then builds the components, so the A2UI is valid by construction.

When you need components a plan cannot express, ask for A2UI directly and let the validator decide:

```bash
a2ui-free generate "Show the forecast" --model ollama/llama3.2:3b --data weather.json          # bound
a2ui-free generate "Show the forecast" --model ollama/llama3.2:3b --data weather.json --inline # sees values
```

Check or repair A2UI from anywhere:

```bash
a2ui-free check surface.json --data data.json --no-literal-figures
a2ui-free repair reply.txt
```

## Any model

A model is one string, so changing provider is a config change. Set it per call or with `A2UI_LLM`.

```text
ollama/llama3.2:3b            lmstudio/...   llamacpp/...   vllm/...
openai/...                    anthropic/...  gemini/...
groq/...  cerebras/...  mistral/...  nvidia/...  openrouter/...
together/...  fireworks/...  deepseek/...  sambanova/...
openai-compatible:https://my.host/v1|my-model
```

Hosted providers read their key from the usual variable, such as `OPENAI_API_KEY`. Join names with commas to fall back when one fails, or pass any function that takes `(system, user)` and returns text.

## What is checked

`validate` fails closed and reports every problem it finds.

- **Schema:** the official A2UI v1.0 JSON schemas and basic catalog.
- **Graph:** a root exists, children exist, nothing is unreachable, no cycles.
- **Bindings:** every path resolves in your data, including inside list templates.
- **Figures:** with `no_literal_figures`, no number typed into text, so every number on screen came from your data.

The test suite has 227 tests. They include 125 of 125 upstream v1.0 conformance cases and a fuzz of 10,000 mangled surfaces.

## What it does not do

- It cannot tell whether a label is true. In the audit, 6 of 207 labels were false, such as "Current Balance" on a person's name. If a wrong label would hurt, have a person review plans, or choose the labels in your own code.
- A plan shows values and tables only. It has no forms, buttons or charts yet.
- It cannot compute. If a screen needs a total or a gap, put it in the data first. Models asked for `spent - budget` as a path, and those plans were refused.

## Reproduce

```bash
git clone https://github.com/Kiragu-Maina/a2ui-free && cd a2ui-free
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/python benchmarks/analyse.py   # recomputes the numbers from stored replies
.venv/bin/a2ui-free bench --models ollama/llama3.2:3b --trials 2 --out results.jsonl
.venv/bin/a2ui-free summary results.jsonl
```

To cite this, see [CITATION.cff](CITATION.cff).

## Licence

MIT. The vendored A2UI schemas and test cases are Apache-2.0 and belong to the A2UI project: see [NOTICE](NOTICE) and [LICENSE-APACHE](LICENSE-APACHE). This project is not affiliated with Google or the A2UI project.

The earlier TypeScript fork, which routed A2UI through free hosted APIs, is kept at the tag `v0-vendored-fork`.
