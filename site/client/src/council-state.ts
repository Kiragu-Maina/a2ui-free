/**
 * State machine for the live council activity panel. Pure data plus a
 * reducer; the Lit component reads it and renders accordingly.
 *
 * The agent's telemetry bus emits a fixed set of event kinds (see
 * council_telemetry.py for the publish sites). The reducer here projects
 * those into a friendly three-step timeline:
 *
 *   1. tool        - the single tool-decision model (only in hybrid mode)
 *   2. council     - parallel fan-out across N members + a judge
 *   3. resolved    - the run is closed, total wall-clock + winner known
 *
 * Members and judge are rendered as children of step 2.
 */

export type StepState = "pending" | "running" | "done" | "error";

export interface MemberRow {
  model: string;
  state: StepState;
  latencyMs?: number;
  snippet?: string;
  errorMessage?: string;
}

export interface ToolStep {
  state: StepState;
  model?: string;
  latencyMs?: number;
  snippet?: string;
  errorMessage?: string;
  startedAt: number;
}

export interface CouncilStep {
  state: StepState;
  strategy?: string;
  judgeModel?: string;
  members: MemberRow[];
  judge?: MemberRow;
  startedAt: number;
}

export interface RunState {
  /** Bumped whenever the user submits a new query. */
  runId: number;
  /** Wall-clock at submit; events older than this are filtered out. */
  startedAtMs: number;
  finishedAtMs?: number;
  tool?: ToolStep;
  council?: CouncilStep;
  winner?: string;
  totalMs?: number;
}

export function makeFreshRun(): RunState {
  return {
    runId: Date.now(),
    startedAtMs: Date.now() - 250,
  };
}

export interface TelemetryEvent {
  event_id: string;
  ts_ms: number;
  kind: string;
  [extra: string]: unknown;
}

function asString(v: unknown): string | undefined {
  return typeof v === "string" ? v : undefined;
}
function asNumber(v: unknown): number | undefined {
  return typeof v === "number" ? v : undefined;
}
function asStringArr(v: unknown): string[] | undefined {
  return Array.isArray(v) && v.every((x) => typeof x === "string")
    ? (v as string[])
    : undefined;
}

/**
 * Apply a telemetry event to the run state. Returns a NEW object so a Lit
 * component can shallow-compare and re-render. Ignores events older than
 * run.startedAtMs to keep stale tail entries from polluting a fresh run.
 */
export function applyEvent(run: RunState, evt: TelemetryEvent): RunState {
  if (evt.ts_ms < run.startedAtMs) return run;
  switch (evt.kind) {
    case "hybrid_route": {
      const route = asString(evt.route);
      if (route === "tool" && !run.tool) {
        return {
          ...run,
          tool: {
            state: "pending",
            model: asString(evt.target_model),
            startedAt: evt.ts_ms,
          },
        };
      }
      if (route === "council" && !run.council) {
        return {
          ...run,
          council: {
            state: "pending",
            judgeModel: undefined,
            members: [],
            startedAt: evt.ts_ms,
          },
        };
      }
      return run;
    }
    case "tool_started": {
      const model = asString(evt.model);
      if (!run.tool) {
        return {
          ...run,
          tool: { state: "running", model, startedAt: evt.ts_ms },
        };
      }
      return { ...run, tool: { ...run.tool, state: "running", model } };
    }
    case "tool_finished": {
      if (!run.tool) return run;
      return {
        ...run,
        tool: {
          ...run.tool,
          state: "done",
          latencyMs: asNumber(evt.latency_ms),
          snippet: asString(evt.response_snippet),
        },
      };
    }
    case "tool_error": {
      if (!run.tool) return run;
      return {
        ...run,
        tool: {
          ...run.tool,
          state: "error",
          latencyMs: asNumber(evt.latency_ms),
          errorMessage: asString(evt.error),
        },
      };
    }
    case "council_started": {
      const members: MemberRow[] = (asStringArr(evt.members) ?? []).map(
        (m) => ({ model: m, state: "pending" }),
      );
      return {
        ...run,
        council: {
          state: "running",
          strategy: asString(evt.strategy),
          judgeModel: asString(evt.judge),
          members,
          startedAt: evt.ts_ms,
        },
      };
    }
    case "member_started": {
      if (!run.council) return run;
      const model = asString(evt.model) ?? "?";
      return {
        ...run,
        council: {
          ...run.council,
          members: updateMember(run.council.members, model, (m) => ({
            ...m,
            state: "running",
          })),
        },
      };
    }
    case "member_finished": {
      if (!run.council) return run;
      const model = asString(evt.model) ?? "?";
      return {
        ...run,
        council: {
          ...run.council,
          members: updateMember(run.council.members, model, (m) => ({
            ...m,
            state: "done",
            latencyMs: asNumber(evt.latency_ms),
            snippet: asString(evt.response_snippet),
          })),
        },
      };
    }
    case "member_error":
    case "member_skipped": {
      if (!run.council) return run;
      const model = asString(evt.model) ?? "?";
      return {
        ...run,
        council: {
          ...run.council,
          members: updateMember(run.council.members, model, (m) => ({
            ...m,
            state: "error",
            latencyMs: asNumber(evt.latency_ms),
            errorMessage:
              asString(evt.error) ??
              asString(evt.reason) ??
              "skipped",
          })),
        },
      };
    }
    case "judge_started": {
      if (!run.council) return run;
      return {
        ...run,
        council: {
          ...run.council,
          judge: {
            model: asString(evt.model) ?? "?",
            state: "running",
          },
        },
      };
    }
    case "judge_finished": {
      if (!run.council) return run;
      const j = run.council.judge ?? { model: "?", state: "pending" as const };
      return {
        ...run,
        council: {
          ...run.council,
          judge: {
            ...j,
            state: "done",
            latencyMs: asNumber(evt.latency_ms),
            snippet: asString(evt.response_snippet),
          },
        },
      };
    }
    case "judge_error":
    case "judge_skipped": {
      if (!run.council) return run;
      const j = run.council.judge ?? { model: "?", state: "pending" as const };
      return {
        ...run,
        council: {
          ...run.council,
          judge: {
            ...j,
            state: "error",
            latencyMs: asNumber(evt.latency_ms),
            errorMessage:
              asString(evt.error) ??
              asString(evt.reason) ??
              "skipped",
          },
        },
      };
    }
    case "council_resolved": {
      const winner = asString(evt.winner) ?? undefined;
      const finishedAtMs = evt.ts_ms;
      return {
        ...run,
        winner,
        finishedAtMs,
        totalMs: finishedAtMs - run.startedAtMs,
        council: run.council
          ? { ...run.council, state: "done" }
          : run.council,
      };
    }
    default:
      return run;
  }
}

function updateMember(
  members: MemberRow[],
  model: string,
  mut: (m: MemberRow) => MemberRow,
): MemberRow[] {
  // If the event arrives before council_started populated the members list
  // (rare race), insert a row for it.
  const idx = members.findIndex((m) => m.model === model);
  if (idx === -1) {
    return [...members, mut({ model, state: "pending" })];
  }
  const next = members.slice();
  next[idx] = mut(next[idx]);
  return next;
}
