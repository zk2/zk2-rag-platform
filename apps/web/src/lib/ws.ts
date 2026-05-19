/** Build WebSocket URL pointing at the API. */
export function wsUrl(path: string): string {
  // In dev, API runs at :8000; in prod we share origin (Next rewrites /api/*).
  const apiBase =
    process.env.NEXT_PUBLIC_API_WS_URL ??
    (typeof window !== "undefined"
      ? `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.hostname}:8000`
      : "ws://localhost:8000");
  return `${apiBase}${path}`;
}
