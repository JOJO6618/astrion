// Shared by both desktop views. Commands never depend on the main document's JS bridge.
export interface NavigationEnvelope {
  command_id: string;
  action: string;
  payload: Record<string, unknown>;
}

export class NavigationError extends Error {
  constructor(
    message: string,
    public status = 0,
    public code = ''
  ) {
    super(message);
  }
}

export function navigationId(): string {
  return crypto.randomUUID();
}

async function request(url: string, init: RequestInit = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 8000);
  const cancel = () => {
    window.clearTimeout(timeout);
    controller.abort();
  };
  if (init.signal?.aborted) cancel();
  init.signal?.addEventListener('abort', cancel, { once: true });
  try {
    const response = await fetch(url, {
      credentials: 'same-origin',
      ...init,
      signal: controller.signal
    });
    const result = await response.json();
    if (!response.ok || !result?.success) {
      throw new NavigationError(
        String(result?.error || response.statusText),
        response.status,
        String(result?.code || '')
      );
    }
    return result;
  } finally {
    window.clearTimeout(timeout);
    init.signal?.removeEventListener('abort', cancel);
  }
}

export class NavigationTransport {
  private tail: Promise<unknown> = Promise.resolve();
  private timer = 0;
  private owner = 0;
  private controller: AbortController | null = null;
  sequence = 0;

  async snapshot() {
    const sequence = ++this.sequence;
    const result = await request('/api/conversation-tabs');
    return { result, sequence };
  }

  command(envelope: NavigationEnvelope) {
    const run = async () => {
      const sequence = ++this.sequence;
      let result;
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          result = await request('/api/conversation-tabs/commands', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(envelope),
            keepalive: true
          });
          return { result, sequence };
        } catch (error) {
          // An uncertain network outcome is retried with the exact same identity.
          if (error instanceof NavigationError || attempt) throw error;
        }
      }
      throw new NavigationError('Navigation request failed');
    };
    const pending = this.tail.then(run, run);
    this.tail = pending.catch(() => {});
    return pending;
  }

  start(apply: (state: any, sequence: number) => void, failed: (error: unknown) => void) {
    this.stop();
    const owner = ++this.owner;
    const tick = async () => {
      const controller = new AbortController();
      this.controller = controller;
      const sequence = ++this.sequence;
      try {
        const result = await request('/api/conversation-tabs', { signal: controller.signal });
        if (owner === this.owner) apply(result, sequence);
      } catch (error) {
        if (owner === this.owner && !controller.signal.aborted) failed(error);
      } finally {
        if (owner === this.owner) {
          this.controller = null;
          this.timer = window.setTimeout(tick, 500);
        }
      }
    };
    void tick();
  }

  stop() {
    this.owner++;
    this.controller?.abort();
    this.controller = null;
    if (this.timer) window.clearTimeout(this.timer);
    this.timer = 0;
  }
}
