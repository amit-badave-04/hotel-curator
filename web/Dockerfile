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
RUN npm run build

# Serve it with nginx as a non-root user on port 8080.
FROM nginxinc/nginx-unprivileged:1.30.6-alpine3.24@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03 AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
USER 101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
