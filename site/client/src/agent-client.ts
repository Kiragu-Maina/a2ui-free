/**
 * Browser-side A2A client. Same shape as the upstream shell/client.ts but
 * cribbed and stripped: no localhost fallback, no markdown handling, no
 * v0.8 awareness. The serverUrl is computed from window.location.origin so
 * a single image works in prod (a2ui-free.alkenacode.dev/agent) and in dev
 * (localhost:5173/agent via vite.config.ts proxy).
 */

import { Part, SendMessageSuccessResponse, Task } from "@a2a-js/sdk";
import { A2AClient } from "@a2a-js/sdk/client";

const A2UI_MIME_TYPE = "application/json+a2ui";
const A2UI_EXT = "https://a2ui.org/a2a-extension/a2ui/v0.9";

export class AgentClient {
  #serverUrl: string;
  #client: A2AClient | null = null;

  constructor(serverUrl?: string) {
    this.#serverUrl = serverUrl ?? `${window.location.origin}/agent`;
  }

  async #getClient(): Promise<A2AClient> {
    if (this.#client) return this.#client;
    this.#client = await A2AClient.fromCardUrl(
      `${this.#serverUrl}/.well-known/agent-card.json`,
      {
        fetchImpl: async (url, init) => {
          const headers = new Headers(init?.headers);
          headers.set("X-A2A-Extensions", A2UI_EXT);
          return fetch(url, { ...init, headers });
        },
      },
    );
    return this.#client;
  }

  /**
   * Send a user message (plain text or a structured A2UI payload) and return
   * the array of A2UI data parts the agent yielded. Empty array = nothing
   * meaningful came back.
   */
  async send(message: string | object): Promise<unknown[]> {
    const client = await this.#getClient();
    const parts: Part[] = [];
    if (typeof message === "string") {
      parts.push({ kind: "text", text: message });
    } else {
      parts.push({
        kind: "data",
        data: message as unknown as Record<string, unknown>,
        mimeType: A2UI_MIME_TYPE,
      } as Part);
    }
    parts.push({
      kind: "data",
      data: { useStreaming: false },
      mimeType: "application/json",
    } as Part);

    const response = await client.sendMessage({
      message: {
        messageId: crypto.randomUUID(),
        role: "user",
        parts,
        kind: "message",
      },
    });

    if ("error" in response) {
      throw new Error(response.error.message);
    }
    const result = (response as SendMessageSuccessResponse).result as Task;
    if (result.kind === "task" && result.status.message?.parts) {
      const payloads: unknown[] = [];
      for (const part of result.status.message.parts) {
        if (part.kind === "data") payloads.push(part.data);
      }
      return payloads;
    }
    return [];
  }
}
