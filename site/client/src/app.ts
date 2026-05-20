/**
 * <a2ui-free-app>
 *
 * Top-level page for a2ui-free.alkenacode.dev. Owns:
 *   - the EventSource to /agent/council-stream
 *   - the current RunState that the council-panel renders from
 *   - the input box + suggested-prompt chips
 *   - the A2A round-trip to the agent
 *   - layout: two-column on >=720px, stacked below
 *
 * Importantly the child components are not coupled to each other: SurfacePane
 * holds its own MessageProcessor, CouncilPanel renders pure data. The app
 * just wires events to state changes.
 */

import { LitElement, css, html, nothing } from "lit";
import { customElement, query, state } from "lit/decorators.js";

import { AgentClient } from "./agent-client.js";
import "./council-panel.js";
import "./surface-pane.js";
import {
  applyEvent,
  makeFreshRun,
  type RunState,
  type TelemetryEvent,
} from "./council-state.js";
import { openCouncilStream } from "./council-stream.js";
import type { CouncilPanel } from "./council-panel.js";
import type { SurfacePane } from "./surface-pane.js";

const SUGGESTED_PROMPTS = [
  "Top 5 Chinese restaurants in New York",
  "3 best Italian restaurants in NY",
  "Mexican restaurants in New York for date night",
];

@customElement("a2ui-free-app")
export class A2uiFreeApp extends LitElement {
  @state()
  accessor #run: RunState | null = null;

  @state()
  accessor #status: "idle" | "running" | "error" = "idle";

  @state()
  accessor #errorMessage: string | null = null;

  @state()
  accessor #streamStatus: "connecting" | "open" | "closed" = "connecting";

  @query("surface-pane")
  private accessor _surface!: SurfacePane;

  @query("council-panel")
  private accessor _council!: CouncilPanel;

  @query("input[name='prompt']")
  private accessor _input!: HTMLInputElement;

  #agent = new AgentClient();
  #stream: ReturnType<typeof openCouncilStream> | null = null;

  connectedCallback() {
    super.connectedCallback();
    this.#stream = openCouncilStream(
      (evt) => this.#onTelemetry(evt),
      (status) => {
        this.#streamStatus = status;
      },
    );
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this.#stream?.close();
  }

  #onTelemetry(evt: TelemetryEvent) {
    if (!this.#run) return;
    this.#run = applyEvent(this.#run, evt);
  }

  async #submit(prompt: string) {
    if (!prompt.trim() || this.#status === "running") return;
    this.#status = "running";
    this.#errorMessage = null;
    this.#run = makeFreshRun();
    this._surface?.clear();
    try {
      const messages = await this.#agent.send(prompt);
      this._surface?.setMessages(messages);
      this.#status = "idle";
    } catch (err) {
      console.error("agent send failed", err);
      this.#errorMessage = err instanceof Error ? err.message : String(err);
      this.#status = "error";
    }
  }

  static styles = css`
    :host {
      display: block;
      font-family: var(--font-sans, system-ui, sans-serif);
      color: var(--ink, #1c1c1c);
      background: var(--bg, #faf8f3);
      min-height: 100vh;
    }
    .page {
      max-width: 1280px;
      margin: 0 auto;
      padding: 40px 24px 80px;
    }
    header {
      text-align: center;
      margin-bottom: 32px;
    }
    h1 {
      font-size: 32px;
      font-weight: 700;
      letter-spacing: -0.01em;
      margin: 0 0 12px 0;
      color: var(--ink, #1c1c1c);
    }
    h1 .accent {
      color: var(--signal, #c97b29);
    }
    .tagline {
      font-size: 16px;
      color: var(--ink-soft, #3a3a3a);
      max-width: 640px;
      margin: 0 auto;
      line-height: 1.6;
    }
    .meta {
      font-size: 13px;
      color: var(--ink-mute, #6b6b6b);
      margin-top: 12px;
    }
    .meta a {
      color: var(--signal, #c97b29);
      text-decoration: none;
      border-bottom: 1px solid currentColor;
    }

    .chips {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: center;
      margin: 28px 0 16px;
    }
    .chip {
      background: var(--bg-raised, #fff);
      color: var(--ink, #1c1c1c);
      border: 1px solid var(--rule, #e2dfd6);
      border-radius: 999px;
      padding: 8px 16px;
      font-size: 13px;
      font-family: inherit;
      cursor: pointer;
      transition: background 120ms, transform 120ms;
    }
    .chip:hover:not([disabled]) {
      background: var(--signal-soft, #f5e7d6);
      border-color: var(--signal, #c97b29);
      transform: translateY(-1px);
    }
    .chip[disabled] {
      opacity: 0.5;
      cursor: not-allowed;
    }

    form {
      display: flex;
      gap: 8px;
      max-width: 720px;
      margin: 0 auto 32px;
    }
    input[name="prompt"] {
      flex: 1;
      padding: 14px 18px;
      border-radius: 999px;
      border: 1px solid var(--rule, #e2dfd6);
      background: var(--bg-raised, #fff);
      color: var(--ink, #1c1c1c);
      font-size: 15px;
      font-family: inherit;
    }
    input[name="prompt"]:focus {
      outline: none;
      border-color: var(--signal, #c97b29);
    }
    button[type="submit"] {
      padding: 14px 24px;
      background: var(--signal, #c97b29);
      color: white;
      border: none;
      border-radius: 999px;
      font-size: 14px;
      font-weight: 600;
      font-family: inherit;
      cursor: pointer;
      transition: opacity 120ms;
    }
    button[type="submit"][disabled] {
      opacity: 0.5;
      cursor: not-allowed;
    }

    .error {
      max-width: 720px;
      margin: 0 auto 24px;
      padding: 12px 16px;
      border-radius: 8px;
      background: rgba(176, 69, 69, 0.1);
      border: 1px solid var(--err, #b04545);
      color: var(--err, #b04545);
      font-size: 13px;
    }

    .grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 16px;
    }
    @media (min-width: 960px) {
      .grid {
        grid-template-columns: 3fr 2fr;
        align-items: start;
      }
    }
  `;

  render() {
    return html`
      <div class="page">
        <header>
          <h1>
            A2UI <span class="accent">free</span>
          </h1>
          <p class="tagline">
            Watch 3 free AIs draft a UI in parallel, then a 4th picks the
            best one. The orchestration happens on Cerebras, Groq, Mistral
            and NVIDIA NIM. Your wallet stays closed.
          </p>
          <p class="meta">
            Built on
            <a href="https://github.com/google/A2UI" target="_blank" rel="noopener">
              Google's A2UI protocol</a>
            ·
            <a href="https://github.com/Kiragu-Maina/a2ui-free" target="_blank" rel="noopener">
              source code</a>
            ·
            <a href="https://kiragu.alkenacode.dev/work/a2ui-free" target="_blank" rel="noopener">
              case study</a>
          </p>
        </header>

        <div class="chips">
          ${SUGGESTED_PROMPTS.map(
            (p) => html`
              <button
                class="chip"
                ?disabled=${this.#status === "running"}
                @click=${() => this.#submit(p)}
              >
                ${p}
              </button>
            `,
          )}
        </div>

        <form
          @submit=${(e: Event) => {
            e.preventDefault();
            this.#submit(this._input.value);
          }}
        >
          <input
            name="prompt"
            type="text"
            placeholder="Ask the restaurant agent something"
            autocomplete="off"
            ?disabled=${this.#status === "running"}
          />
          <button type="submit" ?disabled=${this.#status === "running"}>
            ${this.#status === "running" ? "Working..." : "Send"}
          </button>
        </form>

        ${this.#errorMessage
          ? html`<div class="error">Error: ${this.#errorMessage}</div>`
          : nothing}

        <div class="grid">
          <surface-pane></surface-pane>
          <council-panel
            .run=${this.#run}
            .connectionStatus=${this.#streamStatus}
          ></council-panel>
        </div>
      </div>
    `;
  }
}
