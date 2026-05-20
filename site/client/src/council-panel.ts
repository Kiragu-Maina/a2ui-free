import { LitElement, css, html, nothing } from "lit";
import { customElement, property } from "lit/decorators.js";
import type { CouncilStep, MemberRow, RunState, ToolStep } from "./council-state.js";
import { describeModel } from "./provider-names.js";

@customElement("council-panel")
export class CouncilPanel extends LitElement {
  @property({ attribute: false })
  accessor run: RunState | null = null;

  @property({ type: String })
  accessor connectionStatus: "connecting" | "open" | "closed" = "connecting";

  static styles = css`
    :host {
      display: block;
      font-family: var(--font-sans, system-ui, sans-serif);
      color: var(--ink, #1c1c1c);
    }
    .panel {
      display: flex;
      flex-direction: column;
      gap: 12px;
      padding: 20px;
      background: var(--bg-raised, #fff);
      border: 1px solid var(--rule, #e2dfd6);
      border-radius: 12px;
      min-height: 320px;
    }
    .panel-head {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
    }
    h2 {
      margin: 0;
      font-size: 14px;
      font-weight: 600;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--ink-mute, #6b6b6b);
    }
    .status {
      font-size: 11px;
      font-family: var(--font-mono, monospace);
      color: var(--ink-mute, #6b6b6b);
    }
    .status .dot {
      display: inline-block;
      width: 7px;
      height: 7px;
      border-radius: 50%;
      margin-right: 5px;
      vertical-align: middle;
    }
    .status .dot.open {
      background: var(--ok, #2e7d57);
    }
    .status .dot.connecting {
      background: var(--signal, #c97b29);
      animation: pulse 1s ease-in-out infinite;
    }
    .status .dot.closed {
      background: var(--err, #b04545);
    }
    @keyframes pulse {
      0%, 100% { opacity: 0.4; }
      50% { opacity: 1; }
    }

    .step {
      border: 1px solid var(--rule, #e2dfd6);
      border-radius: 8px;
      padding: 12px 14px;
      background: var(--bg, #faf8f3);
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .step-head {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 13px;
    }
    .step-icon {
      width: 22px;
      height: 22px;
      border-radius: 50%;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 11px;
      font-weight: 700;
      flex-shrink: 0;
    }
    .icon-pending {
      background: transparent;
      border: 1px solid var(--rule, #e2dfd6);
      color: var(--ink-mute, #6b6b6b);
    }
    .icon-running {
      background: var(--signal-soft, #f5e7d6);
      border: 1px solid var(--signal, #c97b29);
      color: var(--signal, #c97b29);
      animation: pulse 1s ease-in-out infinite;
    }
    .icon-done {
      background: var(--ok, #2e7d57);
      color: white;
    }
    .icon-error {
      background: var(--err, #b04545);
      color: white;
    }
    .step-label {
      font-weight: 600;
      font-size: 13px;
      color: var(--ink, #1c1c1c);
    }
    .step-meta {
      margin-left: auto;
      font-family: var(--font-mono, monospace);
      font-size: 11px;
      color: var(--ink-mute, #6b6b6b);
    }
    .step-detail {
      font-size: 12px;
      color: var(--ink-soft, #3a3a3a);
      line-height: 1.5;
      padding-left: 32px;
    }

    .members {
      display: flex;
      flex-direction: column;
      gap: 6px;
      padding-left: 32px;
    }
    .member-row {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 12px;
      padding: 6px 10px;
      border-radius: 6px;
      background: var(--bg-raised, #fff);
      border: 1px solid var(--rule, #e2dfd6);
    }
    .member-name {
      font-weight: 600;
      color: var(--ink, #1c1c1c);
    }
    .member-tag {
      color: var(--ink-mute, #6b6b6b);
      font-size: 11px;
    }
    .member-tag .code {
      font-family: var(--font-mono, monospace);
    }
    .member-latency {
      margin-left: auto;
      font-family: var(--font-mono, monospace);
      font-size: 11px;
      color: var(--ink-mute, #6b6b6b);
    }
    .member-state {
      width: 16px;
      height: 16px;
      border-radius: 50%;
      flex-shrink: 0;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 10px;
      color: white;
    }
    .snippet {
      font-family: var(--font-mono, monospace);
      font-size: 11px;
      line-height: 1.5;
      color: var(--ink-soft, #3a3a3a);
      background: var(--bg, #faf8f3);
      border-left: 2px solid var(--rule, #e2dfd6);
      padding: 6px 10px;
      margin-top: 4px;
      max-height: 80px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .empty {
      color: var(--ink-mute, #6b6b6b);
      font-size: 13px;
      line-height: 1.6;
      padding: 24px 0;
      text-align: center;
    }
    .footer-summary {
      margin-top: 4px;
      padding: 12px 14px;
      background: var(--signal-soft, #f5e7d6);
      border-radius: 8px;
      font-size: 13px;
      color: var(--ink, #1c1c1c);
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 10px;
    }
    .footer-summary .stat {
      font-family: var(--font-mono, monospace);
      font-weight: 700;
      color: var(--signal, #c97b29);
    }
  `;

  render() {
    return html`
      <div class="panel">
        <div class="panel-head">
          <h2>Live council activity</h2>
          <span class="status">
            <span class="dot ${this.connectionStatus}"></span>
            ${this.connectionStatus}
          </span>
        </div>
        ${this.#renderBody()}
      </div>
    `;
  }

  #renderBody() {
    const run = this.run;
    if (!run || (!run.tool && !run.council)) {
      return html`
        <div class="empty">
          Pick a prompt above. You'll watch a tool model decide what to look up,
          then 3 free AIs draft the UI in parallel, then a judge synthesize the
          best one. All on free-tier APIs, $0 per request.
        </div>
      `;
    }
    return html`
      ${run.tool ? this.#renderTool(run.tool) : nothing}
      ${run.council ? this.#renderCouncil(run.council) : nothing}
      ${run.finishedAtMs ? this.#renderFooter(run) : nothing}
    `;
  }

  #renderTool(tool: ToolStep) {
    const desc = describeModel(tool.model);
    return html`
      <div class="step">
        <div class="step-head">
          ${this.#icon(tool.state)}
          <span class="step-label">
            ${this.#toolLabel(tool.state)}
          </span>
          ${tool.latencyMs != null
            ? html`<span class="step-meta">${tool.latencyMs} ms</span>`
            : nothing}
        </div>
        <div class="step-detail">
          ${desc.providerInfo.label}
          <span class="member-tag">
            · <span class="code">${desc.modelCode}</span>
          </span>
        </div>
        ${tool.snippet && tool.state === "done"
          ? html`<div class="snippet">${this.#trim(tool.snippet, 220)}</div>`
          : nothing}
        ${tool.errorMessage
          ? html`<div class="snippet">${tool.errorMessage}</div>`
          : nothing}
      </div>
    `;
  }

  #toolLabel(state: ToolStep["state"]): string {
    switch (state) {
      case "pending":
        return "Routing to tool model";
      case "running":
        return "Looking up restaurants from the database";
      case "done":
        return "Restaurant lookup complete";
      case "error":
        return "Tool model failed";
    }
  }

  #renderCouncil(council: CouncilStep) {
    return html`
      <div class="step">
        <div class="step-head">
          ${this.#icon(council.state)}
          <span class="step-label">
            ${this.#councilLabel(council)}
          </span>
        </div>
        ${council.strategy
          ? html`<div class="step-detail">
              Strategy <span class="member-tag code"
                style="font-family: var(--font-mono, monospace)">
                ${council.strategy}
              </span>
              · ${council.members.length} children in parallel
            </div>`
          : nothing}
        <div class="members">
          ${council.members.map((m) => this.#renderMember(m))}
        </div>
        ${council.judge ? this.#renderJudge(council.judge) : nothing}
      </div>
    `;
  }

  #councilLabel(council: CouncilStep): string {
    if (council.state === "done") {
      return "Council finished drafting";
    }
    if (council.state === "running") {
      return `${council.members.length} free AIs drafting the layout in parallel`;
    }
    return "Council standing by";
  }

  #renderMember(m: MemberRow) {
    const desc = describeModel(m.model);
    return html`
      <div class="member-row">
        ${this.#smallIcon(m.state)}
        <span class="member-name">${desc.providerInfo.label}</span>
        <span class="member-tag">
          <span class="code">${desc.modelCode}</span>
        </span>
        ${m.latencyMs != null
          ? html`<span class="member-latency">${m.latencyMs} ms</span>`
          : nothing}
        ${m.errorMessage
          ? html`<span class="member-tag" style="color: var(--err, #b04545);">
              · ${this.#trim(m.errorMessage, 60)}
            </span>`
          : nothing}
      </div>
    `;
  }

  #renderJudge(judge: MemberRow) {
    const desc = describeModel(judge.model);
    return html`
      <div class="step-head" style="margin-top: 4px;">
        ${this.#icon(judge.state)}
        <span class="step-label">${this.#judgeLabel(judge.state)}</span>
        ${judge.latencyMs != null
          ? html`<span class="step-meta">${judge.latencyMs} ms</span>`
          : nothing}
      </div>
      <div class="step-detail">
        ${desc.providerInfo.label}
        <span class="member-tag">
          · <span class="code">${desc.modelCode}</span>
        </span>
      </div>
    `;
  }

  #judgeLabel(state: MemberRow["state"]): string {
    switch (state) {
      case "pending":
        return "Judge ready";
      case "running":
        return "Judge synthesizing the best draft";
      case "done":
        return "Judge picked a winner";
      case "error":
        return "Judge failed";
    }
  }

  #renderFooter(run: RunState) {
    const seconds = run.totalMs != null ? (run.totalMs / 1000).toFixed(1) : "?";
    const desc = run.winner ? describeModel(run.winner) : null;
    return html`
      <div class="footer-summary">
        <span>
          Done in <span class="stat">${seconds}s</span>
          ${desc ? html`· judge picked <span class="stat">${desc.providerInfo.label}</span>` : nothing}
        </span>
        <span class="stat">$0 · free tier</span>
      </div>
    `;
  }

  #icon(state: StepState) {
    const cls = `step-icon icon-${state}`;
    const glyph =
      state === "done" ? "✓"
      : state === "error" ? "!"
      : state === "running" ? "·"
      : "";
    return html`<span class="${cls}">${glyph}</span>`;
  }

  #smallIcon(state: StepState) {
    const color =
      state === "done" ? "var(--ok, #2e7d57)"
      : state === "error" ? "var(--err, #b04545)"
      : state === "running" ? "var(--signal, #c97b29)"
      : "var(--rule, #e2dfd6)";
    return html`<span class="member-state" style="background:${color};">${
      state === "done" ? "✓" : state === "error" ? "!" : ""
    }</span>`;
  }

  #trim(s: string, max: number): string {
    return s.length > max ? s.slice(0, max) + "…" : s;
  }
}

type StepState = ToolStep["state"];
