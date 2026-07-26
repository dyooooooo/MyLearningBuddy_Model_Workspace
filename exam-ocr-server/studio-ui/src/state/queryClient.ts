import { QueryClient, focusManager } from "@tanstack/query-core";

/** Framework-agnostic Query client for Studio Data Plane. */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 2_000,
      retry: 1,
      refetchOnWindowFocus: true,
    },
  },
});

// Visibility → pause background refetches when tab hidden.
if (typeof document !== "undefined") {
  focusManager.setEventListener((handleFocus) => {
    const onVis = () => handleFocus(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVis, false);
    return () => document.removeEventListener("visibilitychange", onVis);
  });
}
