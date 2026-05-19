# syntax=docker/dockerfile:1.7
# ─── Stage 1: deps ──────────────────────────────────────────
FROM node:22-alpine AS deps
RUN corepack enable && corepack prepare pnpm@9.12.0 --activate
WORKDIR /repo
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml* ./
COPY apps/web/package.json apps/web/
COPY packages/shared-types/package.json packages/shared-types/
COPY packages/ui-config/package.json packages/ui-config/
RUN --mount=type=cache,target=/root/.pnpm-store \
    pnpm install --frozen-lockfile || pnpm install

# ─── Stage 2: builder ───────────────────────────────────────
FROM node:22-alpine AS builder
RUN corepack enable && corepack prepare pnpm@9.12.0 --activate
WORKDIR /repo
COPY --from=deps /repo/node_modules ./node_modules
COPY --from=deps /repo/apps/web/node_modules ./apps/web/node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm --filter web build

# ─── Stage 3: runtime ───────────────────────────────────────
FROM node:22-alpine AS runtime
WORKDIR /app
RUN addgroup -S app && adduser -S app -G app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000

COPY --from=builder /repo/apps/web/.next/standalone /app
COPY --from=builder /repo/apps/web/.next/static /app/apps/web/.next/static
COPY --from=builder /repo/apps/web/public /app/apps/web/public

USER app

EXPOSE 3000
CMD ["node", "apps/web/server.js"]
