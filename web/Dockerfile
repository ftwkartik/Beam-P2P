# Build context is the repository root (see docker-compose.yml), so infra/nginx.conf
# is reachable from this Dockerfile.

FROM node:22-slim AS build

WORKDIR /app

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
# The API/WS URLs are same-origin in production (nginx proxies /api and /ws to the
# server; see infra/nginx.conf), so the client defaults to relative paths when these
# are unset. Only local `npm run dev` needs the absolute localhost URLs in .env.local.
ENV VITE_APP_NAME=Beam
RUN npm run build

FROM nginx:1.27-alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html
COPY infra/nginx.conf /etc/nginx/conf.d/default.conf

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://localhost/health || exit 1

EXPOSE 80
