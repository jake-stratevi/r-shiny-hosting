"""The environment contract, in one place, plus structured logging.

Keeping it in one small module means the Terraform stack in ``../proxy/`` and
the running container can be diffed against each other by reading two files.

Env (exactly this, nothing else):

    AWS_REGION    optional -- boto3 also resolves it from AWS_DEFAULT_REGION
                  and from the task's own metadata. Terraform sets it anyway.
    ECS_CLUSTER   required -- the shared shiny cluster, from SSM.
    APPS_TABLE    required -- shiny-proxy-apps.
    AUDIT_TABLE   required -- shiny-proxy-audit.
    PORT          optional -- default 8080; the ALB target group points here.
    LOG_LEVEL     optional -- default info.

Anything required and missing is a startup failure, not a degraded mode: a
proxy that cannot read the apps table would 404 every app it fronts, which is
worse than not starting.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

DEFAULT_PORT = 8080
DEFAULT_LOG_LEVEL = "info"


class ConfigError(RuntimeError):
    """A deployment error: the environment does not satisfy the contract."""


@dataclass(frozen=True)
class Config:
    """Everything the proxy reads from the environment."""

    cluster: str
    apps_table: str
    audit_table: str
    region: str = ""
    port: int = DEFAULT_PORT
    log_level: str = DEFAULT_LOG_LEVEL


def from_env(env: Mapping[str, str] | None = None) -> Config:
    """Read and validate the contract, or raise :class:`ConfigError`."""
    env = os.environ if env is None else env

    region = (env.get("AWS_REGION") or "").strip()
    cluster = (env.get("ECS_CLUSTER") or "").strip()
    apps_table = (env.get("APPS_TABLE") or "").strip()
    audit_table = (env.get("AUDIT_TABLE") or "").strip()
    log_level = (env.get("LOG_LEVEL") or "").strip().lower() or DEFAULT_LOG_LEVEL

    raw_port = (env.get("PORT") or "").strip() or str(DEFAULT_PORT)
    try:
        port = int(raw_port)
    except ValueError:
        raise ConfigError(f"PORT {raw_port!r} is not a valid port number") from None
    if not 1 <= port <= 65535:
        raise ConfigError(f"PORT {raw_port!r} is not a valid port number")

    missing = [
        name
        for name, value in (
            ("ECS_CLUSTER", cluster),
            ("APPS_TABLE", apps_table),
            ("AUDIT_TABLE", audit_table),
        )
        if not value
    ]
    if missing:
        raise ConfigError("missing required environment: " + ", ".join(missing))

    return Config(
        cluster=cluster,
        apps_table=apps_table,
        audit_table=audit_table,
        region=region,
        port=port,
        log_level=log_level,
    )


# --- logging ---------------------------------------------------------------

# Attributes every LogRecord carries. Anything else on a record came from an
# `extra=` dict at the call site and belongs in the JSON payload.
_STANDARD_RECORD_ATTRS = frozenset(
    """args asctime created exc_info exc_text filename funcName levelname
    levelno lineno message module msecs msg name pathname process
    processName relativeCreated stack_info taskName thread threadName""".split()
)

#: Third-party loggers pinned to WARNING whatever LOG_LEVEL says.
_NOISY_LOGGERS = (
    "aiohttp.access",
    "asyncio",
    "boto3",
    "botocore",
    "urllib3",
    "s3transfer",
)

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


class JsonFormatter(logging.Formatter):
    """One JSON object per line on stdout, for the awslogs driver.

    Structured fields come from ``extra=``; the formatter copies every
    non-standard record attribute into the object, so a call site reads
    ``log.info("refused", extra={"host": host, "outcome": outcome})``.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        # default=str so an unexpected object in `extra` degrades to its repr
        # instead of taking the log line (and the request) down with it.
        return json.dumps(payload, default=str)


def configure_logging(level: str = DEFAULT_LOG_LEVEL) -> logging.Logger:
    """Send JSON to stdout at ``level`` and return the proxy's logger."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(_LEVELS.get(level.strip().lower(), logging.INFO))

    # LOG_LEVEL=debug is for debugging the PROXY. Left alone, botocore alone
    # emits several hundred lines per DynamoDB call and buries every line
    # worth reading. aiohttp.access is muted for a different reason: it
    # duplicates what this service already logs per decision, at a line per
    # asset request, and the ALB's own access log is the record of who asked
    # for what.
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    return logging.getLogger("proxy")
