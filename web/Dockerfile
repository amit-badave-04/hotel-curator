# Build the static bundle.
FROM node:22.23.4-alpine3.24@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS build
WORKDIR /app
ENV CI=true
COPY package.json package-lock.json .npmrc ./
# Behind a TLS-inspecting proxy, pass its CA as an optional build secret:
# see "Building behind a proxy" in the README.
# Without the secret, Node ignores the missing file and uses its own CAs.
RUN --mount=type=secret,id=extra_ca,required=false \
    NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca npm ci --no-audit --no-fund
COPY . .
# The public address in link previews, robots.txt and the sitemap (seo.ts).
# scripts/deploy.py passes the Cloudflare domain in Cloudflare mode.
ARG VITE_PUBLIC_ORIGIN=https://hotel-curator.fly.dev
RUN VITE_PUBLIC_ORIGIN="$VITE_PUBLIC_ORIGIN" npm run build

# Serve it with nginx as a non-root user on port 8080 (8443 in Cloudflare
# mode). The site is rendered at start for CURATOR_EDGE (local, fly or
# cloudflare) by nginx/15-curator-site.sh; see ADR 011.
FROM nginxinc/nginx-unprivileged:1.31.6-alpine3.24@sha256:884e0b7f42f303641b0f4e78755aea647521dca0b89ca3be7992ad78b5c07434 AS runtime
# Alpine fixed brotli-libs (CVE-2026-111497, 1.2.1-r0) after the newest
# build of this image; drop this once the pinned image includes the fix.
USER root
RUN apk upgrade --no-cache brotli-libs
COPY nginx/*.conf nginx/*.template /etc/nginx/curator/
COPY --chmod=0755 nginx/15-curator-site.sh /docker-entrypoint.d/15-curator-site.sh
COPY --from=build /app/dist /usr/share/nginx/html
ENV CURATOR_EDGE=local \
    CURATOR_API_UPSTREAM=api:8000
USER 101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
