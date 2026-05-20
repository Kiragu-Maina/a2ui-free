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
        successful = [r for r in results if r is not None and not _is_error(r)]

        if not successful:
            _TELEMETRY.publish("council_resolved", strategy=strategy, winner=None)
            yield _error("COUNCIL_ALL_FAILED", "All council members errored")
            return

        if strategy == "single-query" or len(successful) == 1:
            _TELEMETRY.publish(
                "council_resolved",
                strategy=strategy,
                winner=getattr(members[0], "model", "?"),
            )
            yield successful[0]
            return

        if strategy == "majority-vote":
            picked = _pick_majority(successful)
            _TELEMETRY.publish(
                "council_resolved",
                strategy=strategy,
                winner=getattr(picked, "model_name", None)
                or "(majority-vote bucket)",
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
        """Ask the judge to merge candidate responses into one."""
        from google.genai import types as genai_types

        rendered = "\n\n".join(
            f"--- Candidate {i + 1} ---\n{_text_of(c)}"
            for i, c in enumerate(candidates)
        )
        prompt = (
            f"You are a quality judge. Below are {len(candidates)} candidate "
            "responses to the same prompt. Synthesize them into a single best "
            "response that combines the strongest elements.\n\n"
            "CRITICAL FORMAT RULES:\n"
            "- Preserve the EXACT output format used by the candidates, "
            "including any wrapping tags (e.g. <a2ui-json>...</a2ui-json>), "
            "code fences, JSON structure, or other markers.\n"
            "- If candidates wrap content in tags or fences, your synthesized "
            "response MUST use the same wrapping.\n"
            "- Do NOT add a preamble, explanation, or any text outside the "
            "synthesized response itself.\n"
            "- Do NOT emit visible reasoning tokens or chain-of-thought.\n\n"
            + rendered
        )

        # Build a fresh LlmRequest for the judge from the original (preserves
        # config defaults) and replace contents + model. We deliberately drop
        # the original tools so the judge produces plain text.
        judge_request = _request_with_model(original, self._judge.model)
        judge_request.contents = [
            genai_types.Content(
                role="user", parts=[genai_types.Part.from_text(text=prompt)]
            )
        ]
        if hasattr(judge_request, "tools"):
            try:
                judge_request.tools = []
            except Exception:
                pass

        verdict = await self._call_child(self._judge, judge_request, role="judge")
        if verdict is None or _is_error(verdict):
            logger.warning("Judge failed; returning first surviving candidate")
            return candidates[0]
        return verdict


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
