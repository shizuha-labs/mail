# Seed the virtualenv from the last verified multi-arch production image. The
# exact lock is synchronized below, so this is an incremental cache source—not
# a dependency source of truth. It avoids re-downloading unchanged wheels when
# a new build-cache repository is cold.
FROM registry.registry.svc.cluster.local:5000/shizuha-mail:mail-acc7faf AS dependency-seed

FROM python:3.10-slim

# Docker Hub is served through Origin's in-cluster pull-through mirror; using
# the unqualified GHCR source bypasses that cache and has repeatedly failed the
# arm64 Kaniko build during GHCR token redirects (ORIG-97).
COPY --from=docker.io/astral/uv:0.11.26 /uv /uvx /bin/

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV UV_PYTHON_DOWNLOADS=0
ENV UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:$PATH"

# Set work directory
WORKDIR /app

# Install exact locked dependencies through the configured package index.
# `uv sync` follows artifact URLs recorded in uv.lock and bypasses an index
# proxy; export exact pins first so Origin's package cache remains authoritative.
COPY pyproject.toml uv.lock /app/
COPY --from=dependency-seed /app/.venv /app/.venv
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=
RUN uv export --frozen --no-dev --no-hashes --no-emit-project --quiet \
        --output-file /tmp/mail-runtime-requirements.txt \
    && if [ -n "$PIP_TRUSTED_HOST" ]; then \
         INSECURE="--allow-insecure-host $PIP_TRUSTED_HOST"; \
       else \
         INSECURE=""; \
       fi \
    && UV_HTTP_TIMEOUT=120 UV_HTTP_RETRIES=5 uv pip sync \
         --python .venv/bin/python \
         --index-url "$PIP_INDEX_URL" \
         $INSECURE \
         /tmp/mail-runtime-requirements.txt \
    && rm -f /tmp/mail-runtime-requirements.txt

# A June emergency deployment patch still launches Supervisor through these
# system paths. Keep a small exec bridge while the Helm-owned command converges
# to the virtualenv paths; no duplicate system Python or Supervisor install is
# needed. The wrapper matters because the old command explicitly invokes it
# with system Python, which otherwise cannot import virtualenv packages.
RUN ln -sf /usr/local/bin/python3 /usr/bin/python3
COPY supervisord-compat.py /usr/bin/supervisord
RUN chmod 0755 /usr/bin/supervisord

# Copy project
COPY . /app/

# Create directories for logs and media
RUN mkdir -p /var/log/django /var/log/supervisor /app/media /app/staticfiles

# Static collection needs Django settings, but no reusable runtime key.
# The disposable process-local value is neither an image ENV nor a build ARG.
RUN SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" POSTGRES_PASSWORD=build-static-only python manage.py collectstatic --noinput

# Create entrypoint script
COPY docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

# Copy supervisord configuration
COPY supervisord.conf /app/

# Expose port
EXPOSE 8014 18114 18115

# Use entrypoint script
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["/app/.venv/bin/supervisord", "-c", "/app/supervisord.conf"]
