# hotel-curator API image: FastAPI + ONNX Runtime, no PyTorch (ADR 007).
# Build context: repository root. Runs as a non-root user.

FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
# Behind a TLS-inspecting proxy, pass a CA bundle as the optional build
# secret "extra_ca" (see "Building behind a proxy" in the README); without it
# pip and uv use their own trust stores.
COPY docker/with-extra-ca.sh /usr/local/bin/with-extra-ca
RUN --mount=type=secret,id=extra_ca,required=false \
    with-extra-ca pip install --no-cache-dir uv==0.11.32
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=secret,id=extra_ca,required=false \
    with-extra-ca uv sync --frozen --no-dev --extra postgres --no-install-project
COPY src ./src
RUN --mount=type=secret,id=extra_ca,required=false \
    with-extra-ca uv sync --frozen --no-dev --extra postgres --no-editable

FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
# Production builds pass REQUIRE_MODEL=1: the image then cannot be built
# unless the served weights are present and are exactly the files the
# promotion rule judged (files_sha256 in promotion.json; BLOCKERS B8).
ARG REQUIRE_MODEL=0
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CURATOR_HOME=/app \
    CURATOR_LOG_FORMAT=json
# The app runs from /opt/venv; the base image's own pip is never used and
# carries known advisories (osv-scanner image scan), so it is removed.
RUN rm -rf /usr/local/lib/python3.12/site-packages/pip \
        /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
        /usr/local/lib/python3.12/ensurepip/_bundled/pip-*.whl \
        /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12 \
    && groupadd --system --gid 10001 curator \
    && useradd --system --uid 10001 --gid curator --home-dir /app curator
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY config ./config
COPY data/demo ./data/demo
COPY artifacts/evals/promotion.json ./artifacts/evals/promotion.json
COPY artifacts/models ./artifacts/models
COPY docker/api-entrypoint.sh /usr/local/bin/api-entrypoint.sh
RUN rm -rf ./artifacts/models/v1-roberta-base \
    && if [ "$REQUIRE_MODEL" = "1" ]; then curator model verify-served; fi \
    && chmod 0755 /usr/local/bin/api-entrypoint.sh \
    && mkdir -p /app/var && chown curator:curator /app/var
# Set after the build-time check above (which runs on the default SQLite
# settings): from here on the app refuses to start without Postgres over TLS
# or a Unix socket.
ENV CURATOR_ENVIRONMENT=production
USER curator
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["/usr/local/bin/api-entrypoint.sh"]
