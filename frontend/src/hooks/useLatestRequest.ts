import { useCallback, useEffect, useRef } from "react";

/**
 * Tells a page whether the answer it just got is still the one it wants.
 *
 * Call the returned function when a request starts. It gives back a check to
 * call when the answer arrives: true only if no newer request has started since
 * and the component is still on screen. Without this, whichever answer arrives
 * last wins, so a slow, older request can overwrite a newer one.
 *
 *     const start = useLatestRequest();
 *     const current = start();
 *     const data = await load();
 *     if (current()) setData(data);
 */
export function useLatestRequest(): () => () => boolean {
  const latest = useRef(0);

  useEffect(() => {
    const counter = latest;
    return () => {
      counter.current += 1;
    };
  }, []);

  return useCallback(() => {
    const mine = ++latest.current;
    return () => mine === latest.current;
  }, []);
}
