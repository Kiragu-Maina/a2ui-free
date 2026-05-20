/**
 * Wraps the A2UI Lit renderer's <a2ui-surface> custom elements in a
 * scrollable, AlkenaCode-themed container. The actual rendering is done by
 * @a2ui/lit; this component just holds the MessageProcessor and lays out
 * whatever surfaces it knows about.
 */

import { provide } from "@lit/context";
import { LitElement, css, html, nothing } from "lit";
import { customElement, state } from "lit/decorators.js";
import { repeat } from "lit/directives/repeat.js";
import { SignalWatcher } from "@lit-labs/signals";

import * as v0_9 from "@a2ui/web_core/v0_9";
import { basicCatalog, Context } from "@a2ui/lit/v0_9";
import { renderMarkdown } from "@a2ui/markdown-it";

type MarkdownRendererFn = (value: string, options?: any) => Promise<string>;

@customElement("surface-pane")
export class SurfacePane extends SignalWatcher(LitElement) {
  @provide({ context: Context.markdown })
  accessor markdownRenderer: MarkdownRendererFn = (val, opts) =>
    Promise.resolve(renderMarkdown(val, opts));

  @state()
  accessor _hasMessages = false;

  #processor = new v0_9.MessageProcessor(
    [basicCatalog],
    async () => {
      /* user actions inside rendered surfaces are no-op for the demo */
    },
  );

  static styles = css`
    :host {
      display: block;
      min-height: 320px;
      font-family: var(--font-sans, system-ui, sans-serif);
      color: var(--ink, #1c1c1c);
    }
    .pane {
      padding: 20px;
      background: var(--bg-raised, #fff);
      border: 1px solid var(--rule, #e2dfd6);
      border-radius: 12px;
      min-height: 320px;
    }
    h2 {
      margin: 0 0 16px 0;
      font-size: 14px;
      font-weight: 600;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--ink-mute, #6b6b6b);
    }
    .empty {
      color: var(--ink-mute, #6b6b6b);
      font-size: 13px;
      line-height: 1.6;
      padding: 32px 0;
      text-align: center;
    }
    .empty strong {
      color: var(--ink, #1c1c1c);
    }
    .surfaces {
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    a2ui-surface {
      display: block;
    }
  `;

  /**
   * Push a fresh batch of A2UI messages from the agent into the processor.
   * Clears existing surfaces first so a new query replaces the previous
   * answer instead of stacking.
   */
  setMessages(messages: any[]): void {
    for (const surfaceId of Array.from(this.#processor.model.surfacesMap.keys())) {
      this.#processor.model.deleteSurface(surfaceId);
    }
    if (messages.length > 0) {
      this.#processor.processMessages(messages);
      this._hasMessages = true;
    } else {
      this._hasMessages = false;
    }
    this.requestUpdate();
  }

  clear(): void {
    for (const surfaceId of Array.from(this.#processor.model.surfacesMap.keys())) {
      this.#processor.model.deleteSurface(surfaceId);
    }
    this._hasMessages = false;
    this.requestUpdate();
  }

  render() {
    const surfaces = Array.from(this.#processor.model.surfacesMap.entries());
    return html`
      <div class="pane">
        <h2>Rendered A2UI surface</h2>
        ${surfaces.length === 0
          ? html`
              <div class="empty">
                <p>
                  <strong>Nothing rendered yet.</strong> The agent emits
                  A2UI JSON, and this pane renders it using the official
                  <code>@a2ui/lit</code> renderer with no extra design work.
                </p>
                <p>Pick a prompt above to see it.</p>
              </div>
            `
          : html`<div class="surfaces">
              ${repeat(
                surfaces,
                ([id]) => id,
                ([, surface]) =>
                  html`<a2ui-surface .surface=${surface}></a2ui-surface>`,
              )}
            </div>`}
      </div>
    `;
  }
}
