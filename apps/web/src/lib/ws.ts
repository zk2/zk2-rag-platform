/** Build WebSocket URL pointing at the API. */
export function wsUrl(path: string): string {
  const configured = process.env.NEXT_PUBLIC_API_WS_URL;
  if (configured) return `${configured}${path}`;
  if (typeof window === "undefined") return `ws://localhost:8000${path}`;

  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  // In production the reverse proxy is the only thing reachable from outside:
  // it serves the API under /api on this same origin, so the socket goes the
  // same way. In dev the API is its own origin on :8000.
  return process.env.NODE_ENV === "production"
    ? `${proto}://${window.location.host}/api${path}`
    : `${proto}://${window.location.hostname}:8000${path}`;
}
