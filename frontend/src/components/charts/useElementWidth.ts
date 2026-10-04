import { useCallback, useState } from "react";

/**
 * Width of an element, kept up to date with a ResizeObserver.
 * Use the returned callback as ``ref``; ``fallback`` is used until the first measure (and in
 * environments without layout, such as unit tests).
 */
export function useElementWidth(fallback = 640): [(node: HTMLElement | null) => void, number] {
  const [width, setWidth] = useState(fallback);
  const ref = useCallback((node: HTMLElement | null) => {
    if (!node) {
      return;
    }
    const measure = (): void => {
      const measured = node.getBoundingClientRect().width;
      if (measured > 0) {
        setWidth(Math.round(measured));
      }
    };
    measure();
    if (typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => {
      observer.disconnect();
    };
  }, []);
  return [ref, width];
}
