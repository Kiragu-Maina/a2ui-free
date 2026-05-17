# Lit shell client (Vite + the A2UI web renderers)
# Build context: repo root.
FROM node:20-slim AS builder

WORKDIR /app

# Workspace deps: shell -> @a2ui/lit -> @a2ui/web_core / markdown-it.
# Preserving the relative path layout so the file:../../ specifiers in
# package.json resolve without sed.
COPY A2UI/renderers                            /app/renderers
COPY A2UI/samples/client/lit/shell             /app/samples/client/lit/shell
COPY A2UI/samples/client/lit/custom-components-example \
                                              /app/samples/client/lit/custom-components-example
COPY A2UI/specification                        /app/specification

# Build renderers in the order the upstream Quickstart prescribes:
RUN cd /app/renderers/web_core \
    && npm install --no-audit --no-fund --legacy-peer-deps \
    && npm run build
RUN cd /app/renderers/markdown/markdown-it \
    && npm install --no-audit --no-fund --legacy-peer-deps \
    && npm run build
RUN cd /app/renderers/lit \
    && npm install --no-audit --no-fund --legacy-peer-deps \
    && npm run build

# Point the shell at the public agent endpoint. Build-arg so the same image
# code can be retargeted (staging vs prod) without editing source.
ARG PUBLIC_AGENT_URL=https://a2ui-free.alkenacode.dev/agent
RUN sed -i "s|http://localhost:10002|${PUBLIC_AGENT_URL}|g" \
    /app/samples/client/lit/shell/configs/restaurant.ts \
    && grep -n "serverUrl" /app/samples/client/lit/shell/configs/restaurant.ts

# Build the shell. The upstream package.json's "build" script runs tsc only,
# so we explicitly invoke vite build after to produce a static bundle.
WORKDIR /app/samples/client/lit/shell
RUN npm install --no-audit --no-fund --legacy-peer-deps \
    && npx tsc -b \
    && npx vite build

# Runtime stage: nginx serving the static dist.
FROM nginx:alpine AS runner
COPY --from=builder /app/samples/client/lit/shell/dist /usr/share/nginx/html
COPY site/client/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
