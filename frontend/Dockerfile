FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json ./
RUN npm install

FROM node:20-alpine AS builder
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
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
