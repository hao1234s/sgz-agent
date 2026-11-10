// Origin-wide invalidation contains only an event type and a random nonce.
const key = "sgz.session-change";
type Phase = "changing" | "changed";
export function sessionEvents(receive: (phase: Phase) => void) {
  let channel: BroadcastChannel | undefined;
  try {
    channel = new BroadcastChannel(key);
  } catch {
    // Storage events remain available.
  }
  let last = "";
  function accept(value: unknown) {
    if (!value || typeof value !== "object") return;
    const event = value as { phase?: unknown; nonce?: unknown };
    if (
      (event.phase !== "changing" && event.phase !== "changed") ||
      typeof event.nonce !== "string" ||
      event.nonce === last
    ) return;
    last = event.nonce;
    receive(event.phase);
  }
  if (channel) channel.onmessage = event => accept(event.data);
  const storage = (event: StorageEvent) => {
    if (event.key !== key || !event.newValue) return;
    try {
      accept(JSON.parse(event.newValue));
    } catch {
      // Malformed events have no authority.
    }
  };
  window.addEventListener("storage", storage);
  return {
    publish(phase: Phase) {
      const event = { phase, nonce: crypto.randomUUID() };
      channel?.postMessage(event);
      try {
        localStorage.setItem(key, JSON.stringify(event));
      } catch {
        // The channel still works without storage.
      }
    },
    close() {
      channel?.close();
      window.removeEventListener("storage", storage);
    },
  };
}
