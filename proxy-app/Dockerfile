# Build:
#   docker buildx build --platform linux/amd64 -t shiny-proxy:dev .
#
# Fargate here is x86_64 (the app images are), so the image is pinned to
# linux/amd64 rather than inherited from whatever laptop builds it.

FROM --platform=linux/amd64 python:3.12-slim

# PYTHONUNBUFFERED so the JSON log lines reach the awslogs driver as they are
# written, not when a pipe buffer fills.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a source-only change does not re-resolve the SDK.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY proxy_app/ ./proxy_app/
COPY seed.py ./

# Non-root. The proxy is in the request path for every app on the platform;
# it needs no privileged port (the ALB target group points at 8080) and no
# write access to anything. Named "shinyproxy" because Debian's base image
# already ships a system user literally called "proxy" (uid 13) -- useradd
# exits 9 on the collision.
RUN useradd --system --create-home --uid 10001 shinyproxy \
    && chown -R shinyproxy:shinyproxy /app
USER shinyproxy

EXPOSE 8080

# The ALB target group health check hits /__proxy/healthz; this is the
# container-level equivalent for `docker run` and ECS's own reporting.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/__proxy/healthz', timeout=2).status == 200 else 1)"

CMD ["python", "-m", "proxy_app"]
