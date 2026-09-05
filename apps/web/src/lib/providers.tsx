"use client";

import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { useErrorBanner } from "@/lib/error-banner";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        // A query that fails has no button to report itself next to, so the
        // failure goes to the shell's banner instead of nowhere
        queryCache: new QueryCache({
          onError: (error) => {
            // 401 is handled by the client: it refreshes, or ends the session
            if (error instanceof ApiError && error.status === 401) return;
            useErrorBanner.getState().report(error.message);
          },
        }),
        defaultOptions: {
          queries: {
            staleTime: 60_000,
            refetchOnWindowFocus: false,
            // Retrying a refusal just makes the user wait for the same answer
            retry: (count, error) =>
              !(error instanceof ApiError && error.status < 500) && count < 1,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
