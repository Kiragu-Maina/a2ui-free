# Experiment: how to ask a small model for a screen

Run on 6 and 7 November 2026. Every number here is recomputed from stored replies by `benchmarks/analyse.py`.

## The question

A model that writes a screen can also write a wrong number into it, and a schema check cannot see that. OWASP lists this class of failure under misinformation and improper output handling.

The first hypothesis was that A2UI data binding fixes it: show the model paths, not values, and it has no number to get wrong. The pilot did not support that as stated. Binding made models much worse at producing a valid screen at all. That led to a third way of asking, the blueprint.

## Method

**Arms**, all using the same request and data:

- **inline:** the model sees the values and writes A2UI with them typed in.
- **bound:** the model sees only paths and writes A2UI that binds to them. Literal figures are refused.
- **blueprint:** the model sees only paths and writes a small plan. Code builds the A2UI. Literal figures are refused.

**Models**, all on Ollama 0.33.3, on an Apple M4 with 16 GB: `gemma3:4b`, `qwen2.5:3b`, `llama3.2:3b`, `qwen2.5:7b`. Temperature 0.2.

**Tasks:** invoice, budget, weather, orders, account, fleet, defined in `src/a2ui_free/bench.py`. All the data is invented, with figures chosen to be easy to tell apart.

**Design:** every model ran every task in every arm, twice, which makes 144 runs. Each run allowed 3 attempts, with validation errors sent back.

**Measures:**

- **Passed:** the final surface passed the validator.
- **Useful:** passed, and at least half of the source figures reached the screen. Without this, a blank screen scores as accurate.
- **Figures not in the data:** figures a reader would see that the source does not hold. The raw field is called `figures_wrong`, which turned out to be too strong a word: see below.

## Results

| Arm | Valid first try | Passed | 95% interval | Useful | 95% interval | Valid and blank | Median s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| inline | 3 | 23 of 48 | 34 to 62% | 17 of 48 | 23 to 50% | 1 | 69 |
| bound | 0 | 8 of 48 | 9 to 30% | 3 of 48 | 2 to 17% | 5 | 53 |
| blueprint before the fix | 38 | 41 of 48 | 73 to 93% | 28 of 48 | 44 to 71% | 11 | 9 |
| blueprint | 28 | 40 of 48 | 70 to 91% | 39 of 48 | 68 to 90% | 0 | 10 |

The intervals are Wilson intervals and treat runs as independent, which they are not quite. Useful runs by model, inline against blueprint: gemma 5 and 8, llama 4 and 9, qwen 3B 4 and 12, qwen 7B 4 and 10. By task: invoice 3 and 8, budget 1 and 6, weather 3 and 6, orders 1 and 8, account 7 and 7, fleet 2 and 4. Blueprint led or tied in every cut. A paired comparison with inline on useful runs gives 23 runs where only blueprint was useful and 1 where only inline was.

### The bug that changed the result

The first blueprint results looked good, with 41 of 48 passing, but 11 of those screens showed no data. Reading the stored replies showed why. Models put a heading, fields and a table in one block, in 29 of 258 blocks. `read_plan` stopped at the heading and dropped the rest without saying so. The check called those screens valid, and they were.

The fix reads each part and refuses a plan that shows no data. The prompt did not change. The blueprint arm was then rerun in full.

**Read the blueprint row with that in mind.** The fix came from these same tasks, and the rerun was not blind. It is a repaired result, not an independent replication. First-try validity fell from 38 to 28 because the reader became stricter, and retries recovered most of that.

### Figures not in the data

Inline showed 11 figures the source does not hold, all on the budget task. They are not all errors.

- **7 were correct sums:** the models worked out differences that were not asked for, such as 1,714 for food over budget (18,214 minus 16,500).
- **4 were false:** one llama run wrote a difference of 3,137 for a true 3,127, 1,764 for 1,714, 1,578 for 1,772, and -3,361 for Transport, which was 359 under budget.

The false run failed validation for a structural reason, so it was stopped by luck. Nothing in a schema catches a subtraction. In the pilot, a llama run on the same task put 7 figures not in the data on a screen that passed every check. Its replies were not stored, so whether those were correct sums or false ones is unknown. Replies have been stored since.

Blueprint's 0 of 336 is guaranteed by construction, since a plan cannot hold a figure. It is a property of the design, and it is not evidence about the models.

### The labels audit

A figure check does not cover labels, so one reader went through all 207 labels on the 40 passing blueprint screens.

- **6 labels were false, on 4 of the 40 screens.** "Current Balance" on the holder's name (twice), "Acme Corp" on the real customer, and "Sunday", "Sunny" and "Tomorrow" on the city.
- **2 more were the placeholder** "Words", copied from the prompt's example.
- **No number sat under another number's label.** Tax never appeared as total, for example. All the false labels were on text values. With 207 labels that is a small sample, not a rule.

So a blueprint moves the risk rather than removing it. Numbers come from the data, while words still come from the model. "Sunny" over "Nairobi" is a claim about the weather that no figure check will see.

### Why plans failed

8 of 48 failed. Four wanted a path that does not exist: `/days[0].high` or a path through a list, such as `/vehicles/fuel`. Two asked for a computed value, `spent - budget`, which is the same pull that produced the sums above. Others refused a list as a single value, and one llama reply had no blocks at all. The budget task shows the design lesson: compute what the screen needs and put it in the data.

## Limits

- **Small models only.** These are 3B to 7B quantized models on one laptop. Larger models may close the gap, and this says nothing about them.
- **Six invented tasks** written by the author, not sampled from real use.
- **Not equally tuned.** A better bound prompt might do better. Bound failed mostly on structure, with unknown paths, unreachable and missing components.
- **Plans do less.** Blueprint only makes fields and tables. Inline can use any catalog component, and the comparison does not reward that.
- **Coverage is rough.** "Half the figures" is a threshold chosen before the main run, and a good screen for "the three biggest orders" need not show everything.
- **Runs are not fully independent.** They repeat by model and task, so treat the paired test as a guide. Two trials per cell at low temperature is thin.
- **Timing is rough.** The main run shared the machine with other builds, and failing runs use all 3 attempts. The 9 and 10 second blueprint figures were measured in different conditions.
- **One reader** judged the labels, and the list is in `benchmarks/analyse.py` for anyone to dispute.

## Files

- `benchmarks/pilot-2026-11-06.jsonl`: 72 pilot runs with inline and bound, no replies stored.
- `benchmarks/results-2026-11-06.jsonl`: the main run, 144 runs, with replies.
- `benchmarks/blueprint-fixed-2026-11-07.jsonl`: the rerun of the blueprint arm, 48 runs, with replies.

To check a model of your own:

```bash
a2ui-free bench --models ollama/your-model --trials 2 --out mine.jsonl
a2ui-free summary mine.jsonl
```

## Sources

- [A2UI specification v1.0](https://github.com/a2ui-project/a2ui/tree/main/specification)
- [OWASP LLM05:2025 Improper Output Handling](https://genai.owasp.org/llmrisk/llm052025-improper-output-handling/)
- [OWASP LLM09:2025 Misinformation](https://genai.owasp.org/llmrisk/llm092025-misinformation/)
