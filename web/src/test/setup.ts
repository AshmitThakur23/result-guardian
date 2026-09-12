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
