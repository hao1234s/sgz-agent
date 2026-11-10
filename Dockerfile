FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY server/package*.json ./server/
COPY frontend/package*.json ./frontend/
RUN npm --prefix server ci && npm --prefix frontend ci
COPY server ./server
COPY frontend ./frontend
RUN npm --prefix server run build && npm --prefix frontend run build

FROM node:22-bookworm-slim AS runtime
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates && rm -rf /var/lib/apt/lists/*
COPY server/package*.json ./server/
RUN npm --prefix server ci --omit=dev --no-audit --no-fund
COPY server/workers/python/pyproject.toml ./server/workers/python/pyproject.toml
COPY server/workers/python/src ./server/workers/python/src
RUN python3 -m venv /app/.venv && /app/.venv/bin/pip install --no-cache-dir /app/server/workers/python
COPY --from=build /app/server/dist ./server/dist
COPY --from=build /app/frontend/dist ./frontend/dist
COPY server/skills/conversation ./server/skills/conversation
RUN mkdir -m 0700 /data && chown node:node /data
ENV NODE_ENV=production HOST=0.0.0.0 PORT=3080 SGZ_DATA_DIR=/data SGZ_COOKIE_SECURE=true
USER node
EXPOSE 3080
HEALTHCHECK --interval=30s --timeout=5s CMD node -e "fetch('http://127.0.0.1:3080/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["sh","-c","umask 077; exec node server/dist/main.js"]
