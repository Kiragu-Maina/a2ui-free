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

"""Council-of-models adapter for ADK.

Queries N child BaseLlm instances in parallel and reduces their responses via
one of four strategies, matching the shellwire LLM Council pattern:

  - single-query     : delegate to the first child (useful as a degenerate case)
  - ranked-fallback  : try children in order, return first that succeeds
  - majority-vote    : whitespace/case-normalized text bucket, return the most common
  - synthesize-best  : ask a judge model to merge all candidates into one response

Limitations
-----------
- No tool-calling. Councils over function-calling agents are unsound (different
  models may call different tools with different args). Use a single model for
  tool-orchestration steps; switch to council mode for the JSON-generation step.
- Streaming is not surfaced. The council waits for terminal responses from each
  child before voting, then yields a single LlmResponse.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import re
from typing import AsyncGenerator, List, Optional

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

from council_telemetry import BUS as _TELEMETRY

logger = logging.getLogger(__name__)

VALID_STRATEGIES = {
    "single-query",
    "ranked-fallback",
    "majority-vote",
    "synthesize-best",
    "resilient",
}

# Per-process quarantine: model_id -> unix-timestamp when it becomes callable again.
# Rate-limit errors quarantine for QUARANTINE_RATELIMIT_S; server errors for
# QUARANTINE_5XX_S; everything else falls through (no quarantine).
import time as _time
_QUARANTINE: dict[str, float] = {}
QUARANTINE_RATELIMIT_S = 60.0
QUARANTINE_5XX_S = 300.0


def _quarantine(model_id: str, seconds: float) -> None:
    if seconds <= 0 or not model_id:
        return
    _QUARANTINE[model_id] = _time.time() + seconds


def _is_quarantined(model_id: str) -> bool:
    until = _QUARANTINE.get(model_id)
    if until is None:
        return False
    if _time.time() >= until:
        _QUARANTINE.pop(model_id, None)
        return False
    return True


_RATELIMIT_PATTERNS = re.compile(
    r"(rate.?limit|429|quota|too many requests|throttle|over.?capacity)",
    re.IGNORECASE,
)
_5XX_PATTERNS = re.compile(
    r"(50[0-9]\b|service unavailable|bad gateway|gateway timeout|internal server error)",
    re.IGNORECASE,
)


def _classify_error_for_quarantine(message: str) -> float:
    """Return quarantine duration in seconds (0 = no quarantine)."""
    if not message:
        return 0.0
    if _RATELIMIT_PATTERNS.search(message):
        return QUARANTINE_RATELIMIT_S
    if _5XX_PATTERNS.search(message):
        return QUARANTINE_5XX_S
    return 0.0


class CouncilLlm(BaseLlm):
    """BaseLlm that fans a request out to N children and reduces the results."""

    def __init__(
        self,
        strategy: str,
        children: List[BaseLlm],
        judge: Optional[BaseLlm] = None,
    ):
        if strategy not in VALID_STRATEGIES:
            raise ValueError(
                f"Unknown council strategy {strategy!r}. "
                f"Valid: {sorted(VALID_STRATEGIES)}"
            )
        if not children:
            raise ValueError("CouncilLlm requires at least one child model")
        super().__init__(model=f"council/{strategy}")
        # NOTE: stored on plain attrs (not pydantic fields) so ADK's BaseLlm
        # validation does not require schema for them.
        object.__setattr__(self, "_strategy", strategy)
        object.__setattr__(self, "_children", children)
        object.__setattr__(self, "_judge", judge or children[0])

    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"council/.*"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        strategy = self._strategy
        member_models = [getattr(c, "model", "?") for c in self._children]
        _TELEMETRY.publish(
            "council_started",
            strategy=strategy,
            members=member_models,
            judge=getattr(self._judge, "model", "?"),
        )

        if strategy == "ranked-fallback":
            # Sequential: short-circuit on first non-error response.
            for child in self._children:
                resp = await self._call_child(child, llm_request)
                if resp is not None and not _is_error(resp):
                    _TELEMETRY.publish(
                        "council_resolved",
                        strategy=strategy,
                        winner=getattr(child, "model", "?"),
                    )
                    yield resp
                    return
            _TELEMETRY.publish("council_resolved", strategy=strategy, winner=None)
            yield _error("COUNCIL_ALL_FAILED", "All council members errored")
            return

        if strategy == "resilient":
            # Like ranked-fallback but logs which member won + skips quarantined
            # members up front (so a stale rate-limit doesn't waste an attempt).
            attempted = 0
            for child in self._children:
                if _is_quarantined(getattr(child, "model", "?")):
                    continue
                attempted += 1
                resp = await self._call_child(child, llm_request)
                if resp is not None and not _is_error(resp):
                    logger.info(
                        "resilient council resolved via %s (after %d attempts)",
                        getattr(child, "model", "?"),
                        attempted,
                    )
                    _TELEMETRY.publish(
                        "council_resolved",
                        strategy=strategy,
                        winner=getattr(child, "model", "?"),
                        attempts=attempted,
                    )
                    yield resp
                    return
            _TELEMETRY.publish(
                "council_resolved",
                strategy=strategy,
                winner=None,
                attempts=attempted,
            )
            yield _error(
                "COUNCIL_ALL_FAILED",
                f"Resilient council exhausted {attempted}/{len(self._children)} members "
                f"(others quarantined). Pool needs refresh or a longer wait.",
            )
            return

        # Parallel for single-query / majority-vote / synthesize-best.
        members = self._children if strategy != "single-query" else self._children[:1]
        results = await asyncio.gather(
            *(self._call_child(c, llm_request) for c in members),
            return_exceptions=False,
        )
        # Keep (child, response) pairs so council_resolved can report the
        # model that actually produced the surviving / winning response,
        # not just members[0] which was wrong when 2+ children were called
        # and a non-first one was the sole survivor.
        successful_pairs = [
            (c, r) for c, r in zip(members, results)
            if r is not None and not _is_error(r)
        ]
        successful = [r for _, r in successful_pairs]

        if not successful:
            _TELEMETRY.publish("council_resolved", strategy=strategy, winner=None)
            yield _error("COUNCIL_ALL_FAILED", "All council members errored")
            return

        if strategy == "single-query" or len(successful) == 1:
            winning_child, winning_resp = successful_pairs[0]
            _TELEMETRY.publish(
                "council_resolved",
                strategy=strategy,
                winner=getattr(winning_child, "model", "?"),
            )
            yield winning_resp
            return

        if strategy == "majority-vote":
            picked = _pick_majority(successful)
            winning_child = next(
                (c for c, r in successful_pairs if r is picked),
                None,
            )
            _TELEMETRY.publish(
                "council_resolved",
                strategy=strategy,
                winner=getattr(winning_child, "model", "?")
                if winning_child is not None
                else "(majority-vote bucket)",
            )
            yield picked
            return

        if strategy == "synthesize-best":
            verdict = await self._synthesize(llm_request, successful)
            _TELEMETRY.publish(
                "council_resolved",
                strategy=strategy,
                winner=getattr(self._judge, "model", "?"),
            )
            yield verdict
            return

    # --- internals -----------------------------------------------------------

    async def _call_child(
        self,
        child: BaseLlm,
        llm_request: LlmRequest,
        role: str = "member",
    ) -> Optional[LlmResponse]:
        """Drain a child's async generator and return the terminal response.

        LiteLlm reads `llm_request.model` (not `self.model`) to decide which
        model id to send. So we shallow-copy the request per child and pin its
        `model` to the child's id; this is safe across parallel children since
        each has its own copy. On rate-limit / 5xx errors the child is
        quarantined for a while so subsequent rounds skip it.

        Publishes telemetry events keyed by `role` ("member" by default, set
        to "judge" by _synthesize) so the SSE consumer can distinguish judge
        timings from council-child timings.
        """
        model_id = getattr(child, "model", "?")
        if _is_quarantined(model_id):
            _TELEMETRY.publish(
                f"{role}_skipped",
                model=model_id,
                reason="quarantined",
            )
            return None
        _TELEMETRY.publish(f"{role}_started", model=model_id)
        started = _time.perf_counter()
        try:
            child_req = _request_with_model(llm_request, child.model)
            final: Optional[LlmResponse] = None
            async for resp in child.generate_content_async(child_req, stream=False):
                final = resp  # keep the latest yield (terminal in non-stream mode)
            elapsed_ms = int((_time.perf_counter() - started) * 1000)
            # Quarantine on logical error (e.g. provider returned 429 wrapped in LlmResponse)
            if final is not None and _is_error(final):
                msg = getattr(final, "error_message", "") or ""
                _quarantine(model_id, _classify_error_for_quarantine(msg))
                _TELEMETRY.publish(
                    f"{role}_error",
                    model=model_id,
                    latency_ms=elapsed_ms,
                    error=msg[:280],
                )
            else:
                # Normalize A2UI markdown fences in candidate responses before
                # telemetry sees them: free models often use ```a2ui-json```
                # instead of the <a2ui-json> tags the upstream parser requires,
                # and the live council panel should show the normalized form.
                if final is not None:
                    final = _apply_a2ui_normalize(final)
                _TELEMETRY.publish(
                    f"{role}_finished",
                    model=model_id,
                    latency_ms=elapsed_ms,
                    response_snippet=_text_of(final)[:280] if final else "",
                )
            return final
        except Exception as exc:
            elapsed_ms = int((_time.perf_counter() - started) * 1000)
            msg = str(exc)
            _quarantine(model_id, _classify_error_for_quarantine(msg))
            logger.warning("Council member %s failed: %s", model_id, msg[:200])
            _TELEMETRY.publish(
                f"{role}_error",
                model=model_id,
                latency_ms=elapsed_ms,
                error=msg[:280],
            )
            return None

    async def _synthesize(
        self, original: LlmRequest, candidates: List[LlmResponse]
    ) -> LlmResponse:
        """Ask the judge to merge candidate responses into one.

        The judge needs to know the OUTPUT FORMAT the caller expects (e.g.
        A2UI requires `{version, createSurface}` / `{version, updateComponents}`
        envelopes, not raw data). We pass through the original system
        instructions so the judge has full context, plus an explicit
        A2UI-shape synthesis directive.
        """
        from google.genai import types as genai_types

        rendered = "\n\n".join(
            f"--- Candidate {i + 1} ---\n{_text_of(c)}"
            for i, c in enumerate(candidates)
        )
        system_text = _extract_system_text(original)

        prompt = (
            f"You are a quality judge. Below are {len(candidates)} candidate "
            "responses to the same prompt, produced by different models. Your "
            "job is to merge them into a SINGLE best response that fully "
            "satisfies the ORIGINAL system instructions (shown above as the "
            "system message of this conversation).\n\n"
            "CRITICAL FORMAT RULES:\n"
            "- Re-read the original system instructions carefully. The output "
            "MUST exactly match the schema/format/structure those instructions "
            "specify. If candidates emit raw data that does NOT match that "
            "schema, you MUST wrap/restructure the data into the correct shape.\n"
            "- For A2UI content specifically: the output MUST be a JSON ARRAY "
            "of A2UI v0.9 message envelopes such as "
            "`{\"version\":\"v0.9\",\"createSurface\":{...}}` and "
            "`{\"version\":\"v0.9\",\"updateComponents\":{...}}` "
            "(or updateDataModel, deleteSurface, etc.), NOT raw business "
            "objects like restaurants or users. Wrap any raw data candidates "
            "produced inside an `updateDataModel` or `updateComponents` "
            "envelope.\n"
            "- A2UI output MUST be wrapped in literal `<a2ui-json>` and "
            "`</a2ui-json>` HTML-style tags. Do NOT use markdown code fences "
            "(```a2ui-json or ```json) for A2UI content.\n"
            "- Do NOT add a preamble, explanation, or any text outside the "
            "synthesized response itself.\n"
            "- Do NOT emit visible reasoning tokens or chain-of-thought.\n\n"
            "CANDIDATES TO SYNTHESIZE:\n"
            + rendered
        )

        # Build the judge request: keep original system instructions, replace
        # the user-turn contents with our synthesis prompt, drop tools (judge
        # generates text only, no further tool calls).
        judge_request = _request_with_model(original, self._judge.model)
        judge_contents = []
        if system_text:
            judge_contents.append(
                genai_types.Content(
                    role="user",
                    parts=[genai_types.Part.from_text(
                        text=f"[SYSTEM INSTRUCTIONS the candidates were responding to]\n\n{system_text}"
                    )],
                )
            )
        judge_contents.append(
            genai_types.Content(
                role="user", parts=[genai_types.Part.from_text(text=prompt)]
            )
        )
        judge_request.contents = judge_contents
        if hasattr(judge_request, "tools"):
            try:
                judge_request.tools = []
            except Exception:
                pass

        verdict = await self._call_child(self._judge, judge_request, role="judge")
        if verdict is None or _is_error(verdict):
            logger.warning("Judge failed; returning first surviving candidate")
            return _apply_a2ui_normalize(candidates[0])
        return _apply_a2ui_normalize(verdict)


# --- module-level helpers ----------------------------------------------------


def _request_with_model(req: LlmRequest, model: str) -> LlmRequest:
    """Return a shallow clone of the request with .model overridden.

    Handles both pydantic-style (model_copy) and plain attribute LlmRequests.
    """
    try:
        # pydantic v2
        return req.model_copy(update={"model": model})  # type: ignore[attr-defined]
    except Exception:
        clone = copy.copy(req)
        try:
            clone.model = model
        except Exception:
            pass
        return clone


def _extract_system_text(req: LlmRequest) -> str:
    """Pull the system prompt out of an LlmRequest across ADK variants.

    Newer ADK stores system instructions in `.config.system_instruction`;
    older ADK threads them through `.contents[*]` with role=system. We check
    both so the judge always sees the same context the council children saw.
    """
    try:
        cfg = getattr(req, "config", None)
        if cfg is not None:
            si = getattr(cfg, "system_instruction", None)
            if si:
                if isinstance(si, str):
                    return si
                parts = getattr(si, "parts", None) or []
                txt = "\n".join(getattr(p, "text", "") or "" for p in parts)
                if txt.strip():
                    return txt
    except Exception:
        pass

    pieces = []
    for content in getattr(req, "contents", []) or []:
        if getattr(content, "role", "") == "system":
            for p in getattr(content, "parts", []) or []:
                t = getattr(p, "text", "") or ""
                if t:
                    pieces.append(t)
    return "\n".join(pieces).strip()


def _is_error(resp: LlmResponse) -> bool:
    return bool(getattr(resp, "error_code", None))


def _error(code: str, message: str) -> LlmResponse:
    return LlmResponse(error_code=code, error_message=message)


def _text_of(resp: LlmResponse) -> str:
    try:
        parts = resp.content.parts  # type: ignore[union-attr]
        for p in parts:
            if getattr(p, "text", None):
                return p.text or ""
    except Exception:
        pass
    return ""


# Free-tier models wrap A2UI JSON inconsistently and frequently emit raw
# data objects alongside (or instead of) valid A2UI v0.9 envelopes. The
# helpers below normalize both: rewrite markdown fences to <a2ui-json> tags
# and strip non-envelope objects so the streaming parser doesn't die on the
# first stray restaurant/user object.
#
# Explicit A2UI fence: ```a2ui-json (or a2uijson / a2ui_json / a2uianything)
# Generic JSON fence:  ```json or bare ``` -- only rewritten if the body
# looks like an A2UI envelope (avoid hijacking unrelated code samples).
_A2UI_FENCE_RE = re.compile(
    r"```\s*a2ui[a-z_-]*\s*\n(.*?)\n```",
    re.IGNORECASE | re.DOTALL,
)
_GENERIC_FENCE_RE = re.compile(
    r"```\s*(?:json)?\s*\n(.*?)\n```",
    re.IGNORECASE | re.DOTALL,
)
# A2UI v0.9 envelope: "version" plus one of the known message-type keys.
_A2UI_SHAPE_RE = re.compile(
    r'"version"\s*:\s*"v0\.[0-9]+"[\s\S]*'
    r'"(createSurface|updateComponents|updateDataModel|deleteSurface|appendComponents|removeComponents|patchComponent|navigate|invokeMethod|setSurfaceState|toast|dismissToast)"',
    re.IGNORECASE,
)
_A2UI_MESSAGE_TYPES = {
    "createSurface", "updateComponents", "updateDataModel", "deleteSurface",
    "appendComponents", "removeComponents", "patchComponent", "navigate",
    "invokeMethod", "setSurfaceState", "toast", "dismissToast",
}
# Already-tagged A2UI block; used to re-enter and filter its contents.
_A2UI_TAG_RE = re.compile(
    r"<a2ui-json>\s*(.*?)\s*</a2ui-json>", re.DOTALL | re.IGNORECASE
)


def _is_a2ui_envelope(obj) -> bool:
    return (
        isinstance(obj, dict)
        and "version" in obj
        and any(k in _A2UI_MESSAGE_TYPES for k in obj.keys())
    )


def _filter_a2ui_block(body: str) -> str:
    """Drop non-envelope objects from an <a2ui-json> block body.

    Free council members sometimes interleave raw data objects (a restaurant,
    a user record) with valid A2UI envelopes. The streaming validator dies
    on the first non-envelope; filter them first so the parser only sees
    envelopes.
    """
    import json as _json
    try:
        parsed = _json.loads(body)
    except _json.JSONDecodeError:
        return body  # not parseable; let the downstream validator complain
    if isinstance(parsed, dict):
        if _is_a2ui_envelope(parsed):
            return body
        logger.warning(
            "council a2ui filter: top-level dict is not an envelope, replacing with []"
        )
        return "[]"
    if isinstance(parsed, list):
        kept = [o for o in parsed if _is_a2ui_envelope(o)]
        if len(kept) == len(parsed):
            return body
        logger.info(
            "council a2ui filter: dropped %d non-envelope objects (kept %d of %d)",
            len(parsed) - len(kept),
            len(kept),
            len(parsed),
        )
        return _json.dumps(kept, indent=2)
    return body


def _normalize_a2ui_format(text: str) -> str:
    """Rewrite markdown fences to <a2ui-json> tags, then envelope-filter.

    Step 1: ```a2ui-json...``` -> <a2ui-json>...</a2ui-json> (always).
    Step 2: ```json...``` / bare ```...``` -> <a2ui-json>...</a2ui-json>
            ONLY if the body looks like an A2UI envelope (else leave alone).
    Step 3: Inside every <a2ui-json> block, drop objects that aren't valid
            v0.9 envelopes.
    """
    if not text:
        return text
    text = _A2UI_FENCE_RE.sub(
        lambda m: f"<a2ui-json>\n{m.group(1).strip()}\n</a2ui-json>",
        text,
    )
    def _maybe_a2ui(m):
        body = m.group(1).strip()
        return (
            f"<a2ui-json>\n{body}\n</a2ui-json>"
            if _A2UI_SHAPE_RE.search(body)
            else m.group(0)
        )
    text = _GENERIC_FENCE_RE.sub(_maybe_a2ui, text)
    text = _A2UI_TAG_RE.sub(
        lambda m: f"<a2ui-json>\n{_filter_a2ui_block(m.group(1))}\n</a2ui-json>",
        text,
    )
    return text


def _apply_a2ui_normalize(resp: LlmResponse) -> LlmResponse:
    """Walk a response's text parts and normalize A2UI markdown / envelopes.

    Mutates `resp.content.parts[i].text` in place, matching the existing
    in-place pattern used by HybridToolCouncilLlm._strip_function_calls.
    Returns the same response so callers can chain.
    """
    try:
        content = getattr(resp, "content", None)
        if not content:
            return resp
        parts = getattr(content, "parts", None) or []
        for p in parts:
            txt = getattr(p, "text", None)
            if txt and "a2ui" in txt.lower():
                new = _normalize_a2ui_format(txt)
                if new != txt:
                    p.text = new
    except Exception as exc:
        logger.debug("a2ui normalize: %s", exc)
    return resp


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _pick_majority(responses: List[LlmResponse]) -> LlmResponse:
    """Bucket responses by normalized-text hash; return any vote from the largest bucket."""
    buckets: dict[str, List[LlmResponse]] = {}
    for r in responses:
        key = hashlib.sha1(_normalize(_text_of(r)).encode("utf-8")).hexdigest()[:16]
        buckets.setdefault(key, []).append(r)
    winner = max(buckets.values(), key=len)
    return winner[0]


# ============================================================================
# HybridToolCouncilLlm: route tool-decision turns to a single model, UI-generation
# turns to a council. Solves the "council can't vote on function_call args"
# problem documented in CouncilLlm's docstring.
# ============================================================================


class HybridToolCouncilLlm(BaseLlm):
    """Routes tool-decision turns to a single tool_model and post-tool turns to a council.

    Heuristic
    ---------
    If any content in the request contains a `function_response` part, the agent
    has already gathered tool data and is now formulating the final answer; this
    request goes to the council. Otherwise (first turn, no prior tool calls) the
    request goes to the tool_model so it can decide what tool to call (if any).

    Constraints
    -----------
    - When routed to council, function_call parts in the council's response are
      stripped to prevent the council members from triggering further tool calls
      that they can't agree on. Override with strip_function_calls=False.
    - With multi-step tool use (call A -> response -> call B -> response -> answer),
      this heuristic routes every post-first-tool turn to council, so council
      cannot decide on call B. For multi-step tool agents, keep using a single
      model end-to-end and switch to council only for the rendering step.
    """

    def __init__(
        self,
        tool_model: BaseLlm,
        council: BaseLlm,
        strip_function_calls: bool = True,
    ):
        super().__init__(model=f"hybrid/{getattr(tool_model, 'model', '?')}+{getattr(council, 'model', '?')}")
        object.__setattr__(self, "_tool", tool_model)
        object.__setattr__(self, "_council", council)
        object.__setattr__(self, "_strip", strip_function_calls)

    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"hybrid/.*"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        wants_council = self._has_function_response(llm_request)
        target = self._council if wants_council else self._tool
        logger.info(
            "hybrid routing -> %s (function_response in request: %s)",
            getattr(target, "model", "?"),
            wants_council,
        )
        _TELEMETRY.publish(
            "hybrid_route",
            route="council" if wants_council else "tool",
            target_model=getattr(target, "model", "?"),
        )
        # Same fix as CouncilLlm._call_child: LiteLlm reads request.model, not
        # self.model, so we must rewrite the request to the target's model id.
        # CouncilLlm itself does this rewrite internally per child, so when
        # target is the council we leave the request alone and the council
        # rewrites for each of its children.
        forwarded = (
            llm_request
            if isinstance(target, CouncilLlm)
            else _request_with_model(llm_request, target.model)
        )
        # For the tool-turn path (target is a single BaseLlm, not a council)
        # emit explicit tool_started/tool_finished events so the UI can show
        # the tool-decision step. Council-turn path lets CouncilLlm publish
        # its own council_started/member_* events.
        instrument_single = not isinstance(target, CouncilLlm)
        tool_started_at = _time.perf_counter() if instrument_single else 0.0
        if instrument_single:
            _TELEMETRY.publish("tool_started", model=getattr(target, "model", "?"))
        final: Optional[LlmResponse] = None
        try:
            async for resp in target.generate_content_async(forwarded, stream=stream):
                if wants_council and self._strip:
                    self._strip_function_calls(resp)
                final = resp
                yield resp
        except Exception as exc:
            if instrument_single:
                _TELEMETRY.publish(
                    "tool_error",
                    model=getattr(target, "model", "?"),
                    latency_ms=int((_time.perf_counter() - tool_started_at) * 1000),
                    error=str(exc)[:280],
                )
            raise
        if instrument_single:
            _TELEMETRY.publish(
                "tool_finished",
                model=getattr(target, "model", "?"),
                latency_ms=int((_time.perf_counter() - tool_started_at) * 1000),
                response_snippet=_text_of(final)[:280] if final else "",
            )

    @staticmethod
    def _has_function_response(request: LlmRequest) -> bool:
        contents = getattr(request, "contents", []) or []
        for content in contents:
            for part in getattr(content, "parts", []) or []:
                if getattr(part, "function_response", None):
                    return True
        return False

    @staticmethod
    def _strip_function_calls(resp: LlmResponse) -> None:
        """Remove function_call parts in-place so council members can't trigger tool calls."""
        try:
            content = getattr(resp, "content", None)
            if not content:
                return
            parts = getattr(content, "parts", None)
            if not parts:
                return
            kept = [p for p in parts if not getattr(p, "function_call", None)]
            if len(kept) != len(parts):
                content.parts = kept
        except Exception as exc:
            logger.debug("strip_function_calls: %s", exc)
