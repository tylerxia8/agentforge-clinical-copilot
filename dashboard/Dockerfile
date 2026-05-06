# Multi-stage Dockerfile for the Next.js dashboard.
# Stage 1: install + build using the full devDependencies tree.
# Stage 2: ship only the standalone output (smaller image, fewer
# packages on the runtime path = smaller attack surface).

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
# Install dependencies. We use ci when there's a lockfile (deterministic),
# fall back to install for the first build before anyone has run npm i.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000

# next.config.ts sets `output: "standalone"` so the build emits
# /app/.next/standalone with a self-contained server.js + the
# minimal node_modules subset it actually uses.
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000

# Bind to all interfaces so Railway's edge can reach the container.
CMD ["node", "server.js"]
