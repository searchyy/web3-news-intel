import type { QueryClientConfig } from "@tanstack/react-query";
import { useEffect, useState } from "react";

export const QUERY_STALE_TIME = {
  default: 30_000,
  dashboardSummary: 30_000,
  eventsList: 5_000,
  eventFacets: 300_000,
  eventInsight: 60_000,
  savedSearches: 60_000,
  sources: 60_000
} as const;

export const QUERY_REFETCH_INTERVAL = {
  visibleEventsList: 10_000
} as const;

export function shouldRetryQuery(failureCount: number, error: unknown) {
  if (isAuthenticationError(error)) {
    return false;
  }
  return failureCount < 1;
}

export function visibleOnlyRefetchInterval(
  intervalMs: number,
  visible = isDocumentVisible(),
  failureCount = 0
) {
  if (!visible) {
    return false;
  }
  if (failureCount >= 3) {
    return false;
  }
  return Math.min(intervalMs * 2 ** failureCount, 60_000);
}

export function isDocumentVisible() {
  return typeof document === "undefined" || document.visibilityState === "visible";
}

export function useDocumentVisible() {
  const [visible, setVisible] = useState(isDocumentVisible);

  useEffect(() => {
    if (typeof document === "undefined") {
      return undefined;
    }
    const handleVisibilityChange = () => setVisible(isDocumentVisible());
    document.addEventListener("visibilitychange", handleVisibilityChange);
    handleVisibilityChange();
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, []);

  return visible;
}

function isAuthenticationError(error: unknown) {
  return error instanceof Error && (error.message === "Authentication required" || /^HTTP 401\b/.test(error.message));
}

export const queryClientConfig: QueryClientConfig = {
  defaultOptions: {
    queries: {
      staleTime: QUERY_STALE_TIME.default,
      refetchOnWindowFocus: false,
      retry: shouldRetryQuery
    }
  }
};
