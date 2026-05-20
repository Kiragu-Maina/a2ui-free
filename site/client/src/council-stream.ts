/**
 * Thin EventSource wrapper. Opens /agent/council-stream and emits parsed
 * events through a callback. Auto-reconnects on transient errors.
 */

import type { TelemetryEvent } from "./council-state.js";

export interface CouncilStreamHandle {
  close(): void;
}

export function openCouncilStream(
  onEvent: (evt: TelemetryEvent) => void,
  onStatusChange?: (status: "connecting" | "open" | "closed") => void,
): CouncilStreamHandle {
  const url = `${window.location.origin}/agent/council-stream`;
  let es: EventSource | null = null;
  let closed = false;
  let reconnectDelay = 1000;

  const connect = () => {
    if (closed) return;
    onStatusChange?.("connecting");
    es = new EventSource(url);
    es.onopen = () => {
      reconnectDelay = 1000;
      onStatusChange?.("open");
    };
    es.onmessage = (msg) => {
      try {
        const parsed = JSON.parse(msg.data) as TelemetryEvent;
        if (parsed && typeof parsed === "object" && parsed.kind) {
          onEvent(parsed);
        }
      } catch (err) {
        console.warn("council-stream: bad event", msg.data, err);
      }
    };
    es.onerror = () => {
      es?.close();
      es = null;
      if (closed) {
        onStatusChange?.("closed");
        return;
      }
      // Exponential-ish backoff capped at 10s. EventSource auto-reconnects
      // on its own but does so by re-running the full handshake even when
      // the server is genuinely down; setting `closed` here lets us
      // back off explicitly.
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 1.6, 10_000);
    };
  };

  connect();

  return {
    close() {
      closed = true;
      es?.close();
      es = null;
      onStatusChange?.("closed");
    },
  };
}
