import "@testing-library/jest-dom/vitest";
import "@/i18n";

// jsdom has no ResizeObserver; Radix measures popovers and tooltips with it.
if (!("ResizeObserver" in globalThis)) {
  globalThis.ResizeObserver = class {
    observe = (): void => undefined;
    unobserve = (): void => undefined;
    disconnect = (): void => undefined;
  };
}
