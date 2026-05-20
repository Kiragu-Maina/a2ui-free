# Custom Lit shell for a2ui-free.alkenacode.dev. Two stages:
# 1. builder: build the @a2ui/web_core, @a2ui/markdown-it, @a2ui/lit renderers
#    then build the site/client/ Vite app that depends on them via file: deps.
# 2. runner: nginx:alpine serving the static dist + the SSE-friendly conf.
#
# Build context: repo root (..). The upstream A2UI/samples/client/lit/shell
# tree is no longer consumed.
FROM node:20-slim AS builder

WORKDIR /app

# Renderer packages + their schema dependency. file: paths in
# site/client/package.json resolve to /app/A2UI/renderers/... so we keep
# the upstream layout intact below /app/A2UI/.
COPY A2UI/renderers      /app/A2UI/renderers
COPY A2UI/specification  /app/A2UI/specification

# Build renderers in the order the upstream Quickstart documents. Each
# package's dist/ must exist before site/client npm install can resolve
# its file: deps to a real entry point.
RUN cd /app/A2UI/renderers/web_core \
    && npm install --no-audit --no-fund --legacy-peer-deps \
    && npm run build
RUN cd /app/A2UI/renderers/markdown/markdown-it \
    && npm install --no-audit --no-fund --legacy-peer-deps \
    && npm run build
RUN cd /app/A2UI/renderers/lit \
    && npm install --no-audit --no-fund --legacy-peer-deps \
    && npm run build

# Strip prepack scripts from renderer package.jsons so site/client's
# `npm install` does NOT re-run their wireit builds (devDeps aren't
# installed at the file: dep boundary, so the rebuild would fail anyway).
# We already have valid dist/ output from the explicit builds above.
RUN for pkg in /app/A2UI/renderers/web_core /app/A2UI/renderers/markdown/markdown-it /app/A2UI/renderers/lit; do \
        if [ -f "$pkg/package.json" ]; then \
            sed -i '/"prepack":/d' "$pkg/package.json"; \
        fi; \
    done

# Custom site/client app. Copied AFTER the renderer builds so a tweak in
# src/ doesn't bust the slow renderer install cache.
COPY site/client /app/site/client
WORKDIR /app/site/client
RUN npm install --no-audit --no-fund --legacy-peer-deps \
    && npm run build

# Runtime stage: nginx serving the static dist.
FROM nginx:alpine AS runner
COPY --from=builder /app/site/client/dist /usr/share/nginx/html
COPY site/client/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
