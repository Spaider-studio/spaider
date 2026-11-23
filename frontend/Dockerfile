FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:24-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
# Next.js bakes the rewrite destination (next.config.mjs) into the build, so
# this must point at the backend as seen FROM the frontend container, not the
# host. In the compose stack that is the Kong gateway service. localhost would
# resolve to the frontend container itself (ECONNREFUSED). Override at build
# time for other topologies.
ARG BACKEND_API_URL=http://kong:8000/api
ENV BACKEND_API_URL=${BACKEND_API_URL}
# Where the browser reaches the API directly (uploads, exports, long LLM calls).
# NEXT_PUBLIC_* values are fixed at build time. Empty means "the host the Studio
# was opened on, port 8000".
ARG NEXT_PUBLIC_BACKEND_URL=
ENV NEXT_PUBLIC_BACKEND_URL=${NEXT_PUBLIC_BACKEND_URL}
RUN npm run build

FROM node:24-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

RUN addgroup -g 10001 spaider && adduser -D -u 10001 -G spaider spaider
USER 10001

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
