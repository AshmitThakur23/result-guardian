import "@testing-library/jest-dom/vitest";

// jsdom implements neither. The gate calls scrollIntoView when it moves the
// doctor to an incomplete row, and matchMedia is reached through Radix.
window.HTMLElement.prototype.scrollIntoView = () => {};

if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

// React Router builds a `Request` for every navigation, even for routes with
// no loaders. Under vitest's jsdom environment that hits a genuine interop
// bug: the `init.signal` is jsdom's AbortSignal, while `Request` resolves to
// Node's undici implementation, which rejects it as "not an instance of
// AbortSignal". Navigation then throws and the destination never renders.
//
// Nothing in this application constructs a Request -- `api/client.ts` calls
// fetch with a plain string -- so a minimal stand-in is enough to let the
// router navigate, and it cannot mask an application bug.
class TestRequest {
  readonly url: string;
  readonly method: string;
  readonly signal: unknown;
  constructor(input: string | URL, init: { method?: string; signal?: unknown } = {}) {
    this.url = String(input);
    this.method = init.method ?? "GET";
    this.signal = init.signal;
  }
}
globalThis.Request = TestRequest as unknown as typeof globalThis.Request;
