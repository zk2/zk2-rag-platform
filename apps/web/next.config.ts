import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV !== "production";

/**
 * Content policy for the browser app.
 *
 * `unsafe-inline` for styles is what Tailwind's runtime style injection and
 * React's inline styles need; scripts additionally need `unsafe-eval` only in
 * development, where Next's fast refresh compiles in the browser. Everything
 * else is denied, and the app never loads a script from another origin.
 */
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  // Same origin covers the /api rewrite; ws: is the chat socket
  `connect-src 'self' ${isDev ? "ws: http:" : "wss:"}`,
  "frame-ancestors 'none'",
  "base-uri 'none'",
  "form-action 'self'",
  "object-src 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
];

const config: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.API_BASE_URL ?? "http://localhost:8000"}/:path*`,
      },
    ];
  },
};

export default config;
