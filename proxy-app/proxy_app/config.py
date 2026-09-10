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
    PORTAL_HOSTS  optional -- comma-separated hostnames served by the portal
                  (docs/design/portal-api.md) instead of being proxied to an
                  app. Empty (the default) means the portal is off entirely
                  and this service is exactly the proxy it was before.
    PORTAL_DIST   optional -- default ./portal-dist; the directory holding the
                  built React bundle. A missing directory is NOT an error:
                  the API still answers and `/` serves a placeholder, so the
                  backend is deployable before portal-ui/ exists.

Sign-out (``/__proxy/logout``) reads two more, optional TOGETHER and
required TOGETHER for the same reason the creation block is -- half of a
sign-out is a sign-out that does not work:

    COGNITO_DOMAIN     the hosted UI's domain. Either the PREFIX Cognito
                       registered (the value platform/ssm.tf exports as
                       `cognito_domain`, e.g. "stratevi-hub"), in which case
                       AWS_REGION completes it into
                       <prefix>.auth.<region>.amazoncognito.com, or a full
                       custom domain hostname if one is ever adopted. Same
                       semantics as the ALB action's `user_pool_domain`.
    COGNITO_CLIENT_ID  the shared app client -- the SAME variable the
                       creation block reads, deliberately read again here so
                       sign-out does not depend on the creation pipeline
                       being deployed.
    SIGNED_OUT_URL     optional override for the `logout_uri` Cognito sends
                       the browser back to. Default:
                       https://<the portal host the request arrived on>
                       /__proxy/signed-out. Set it only if a different URL is
                       what got registered in the client's LogoutURLs --
                       Cognito matches that list EXACTLY and refuses anything
                       else with an error page.

P2a self-service creation (docs/design/portal-p2a.md) adds one more block,
all of it optional TOGETHER and required TOGETHER. Unlike everything above,
a partial block DISABLES creation and logs rather than failing startup --
see :class:`Creation` for why that one exception exists:

    UPLOADS_BUCKET         shiny-portal-uploads-<acct>; presigned PUT target.
    CODEBUILD_PROJECT      shiny-app-build; the one shared build project.
    APP_ROLE_BOUNDARY_ARN  the permissions boundary EVERY created role
                           carries. Without it, creation is off -- there is
                           no mode in which a role is created unfenced.
    APP_DATA_BUCKET        shiny-app-data-<acct>; each app's role may read
                           only its own <app-key>/ prefix.
    APP_DOMAIN             tools.stratevi.com; <key>.<APP_DOMAIN> is the host.
    APP_SUBNET_IDS         comma-separated; the created service's subnets.
    APP_SECURITY_GROUP_ID  the shared apps SG the proxy is allowed into.
    APP_EXECUTION_ROLE_ARN the shared platform execution role (image pull +
                           log writes), same one every app stack uses.
    APP_LOG_GROUP          the log group created apps write to.
    COGNITO_USER_POOL_ID   the Hub pool.
    COGNITO_CLIENT_ID      the ONE shared app client whose callback list
                           grows by one URL per created app.

Anything required and missing is a startup failure, not a degraded mode: a
proxy that cannot read the apps table would 404 every app it fronts, which is
worse than not starting.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping

from .registry import normalize_host

DEFAULT_PORT = 8080
DEFAULT_LOG_LEVEL = "info"

#: Where the built React bundle lives inside the image. The Dockerfile creates
#: it empty so the image builds before portal-ui/ exists; the CI/build step
#: copies portal-ui/dist over it.
DEFAULT_PORTAL_DIST = "./portal-dist"


class ConfigError(RuntimeError):
    """A deployment error: the environment does not satisfy the contract."""


@dataclass(frozen=True)
class SignOut:
    """What ``/__proxy/logout`` needs to end the Cognito session too.

    Signing out of this platform is two acts, not one. Expiring the ALB's
    ``AWSELBAuthSessionCookie`` shards is the half this service can always
    do; ending the Cognito hosted-UI session is the half that needs to know
    where that hosted UI lives, and without it the ALB's very next
    authenticate action gets a still-valid Cognito session back and signs
    the user straight in again.

    Optional as a BLOCK and degrading rather than fatal, for the same reason
    :class:`Creation` is: this task fronts every app on the platform and must
    not refuse to boot over a sign-out variable. What degrades is honest --
    the cookies are still expired, and the person is told the SSO session was
    not ended -- rather than a redirect to a URL assembled from a guess.
    """

    #: Either a hosted-UI prefix ("stratevi-hub") or a full custom hostname.
    #: Which one it is decided by whether it contains a dot, exactly as the
    #: ALB's own `user_pool_domain` decides it.
    domain: str
    client_id: str
    #: Needed only to complete the prefix form. Empty is valid when `domain`
    #: is already a full hostname.
    region: str = ""
    #: Empty means "derive it from the request's own portal host".
    signed_out_url: str = ""


#: The env names :class:`SignOut` requires together.
SIGNOUT_VARIABLES = ("COGNITO_DOMAIN", "COGNITO_CLIENT_ID")


@dataclass(frozen=True)
class Creation:
    """The P2a creation pipeline's environment, all of it or none of it.

    All-or-nothing at the FEATURE level: either every variable is present and
    creation works, or creation is off entirely. There is no partial mode,
    because a pipeline that fails at its third API call has already reserved
    a hostname and created an ECR repository, which is far more expensive to
    clean up than a feature that never started.

    A partial environment disables creation and logs loudly -- it is NOT a
    startup failure, unlike everything else in this module. The reason is
    blast radius: this task is in the request path for every app on the
    platform, and refusing to boot over a misconfigured *creation* variable
    would take the dashboard, the model and the portal down to protect a
    wizard nobody is currently using. See :attr:`Config.creation_error`,
    which `__main__` logs at ERROR on the way up.
    """

    uploads_bucket: str
    codebuild_project: str
    #: The Terraform-owned permissions boundary. Its presence in this REQUIRED
    #: set is the outermost layer of the boundary invariant: with no boundary
    #: there is no creation, so there is no path to an unfenced app role.
    #: See provision.Boto3TaskRoles' banner for the other three layers.
    role_boundary_arn: str
    data_bucket: str
    domain: str
    subnet_ids: tuple[str, ...]
    security_group_id: str
    execution_role_arn: str
    log_group: str
    user_pool_id: str
    client_id: str


#: The env names :class:`Creation` reads, in the order a missing-variable
#: message should list them.
CREATION_VARIABLES = (
    "UPLOADS_BUCKET",
    "CODEBUILD_PROJECT",
    "APP_ROLE_BOUNDARY_ARN",
    "APP_DATA_BUCKET",
    "APP_DOMAIN",
    "APP_SUBNET_IDS",
    "APP_SECURITY_GROUP_ID",
    "APP_EXECUTION_ROLE_ARN",
    "APP_LOG_GROUP",
    "COGNITO_USER_POOL_ID",
    "COGNITO_CLIENT_ID",
)


@dataclass(frozen=True)
class Config:
    """Everything the proxy reads from the environment."""

    cluster: str
    apps_table: str
    audit_table: str
    region: str = ""
    port: int = DEFAULT_PORT
    log_level: str = DEFAULT_LOG_LEVEL
    #: Hostnames the portal answers on, normalized the same way a Host header
    #: is. Empty means no portal.
    portal_hosts: tuple[str, ...] = field(default_factory=tuple)
    portal_dist: str = DEFAULT_PORTAL_DIST
    #: None until the P2a Terraform lands. The P2a routes answer 503 while it
    #: is None -- they are advertised in the contract, so 404 would be a lie.
    creation: Creation | None = None
    #: Why creation is off, when it is off for a REASON rather than because
    #: nobody asked for it. Empty when the block is simply absent.
    creation_error: str = ""
    #: None until COGNITO_DOMAIN lands. Sign-out still expires the ALB
    #: cookies without it; it just cannot end the Cognito session as well.
    signout: SignOut | None = None
    #: Why sign-out is half-configured, when it is. Same contract as
    #: `creation_error`: empty when the block is simply absent.
    signout_error: str = ""

    def portal_enabled(self) -> bool:
        return bool(self.portal_hosts)

    def creation_enabled(self) -> bool:
        return self.creation is not None

    def signout_enabled(self) -> bool:
        return self.signout is not None


def parse_portal_hosts(raw: str) -> tuple[str, ...]:
    """Split PORTAL_HOSTS and normalize each entry.

    Normalized through the same function the request path uses, so
    "Dashboards.Tools.Stratevi.com" in Terraform still matches the Host header
    that arrives. Duplicates collapse; order is preserved for legibility in
    the startup log.
    """
    seen: list[str] = []
    for chunk in (raw or "").split(","):
        host = normalize_host(chunk)
        if host and host not in seen:
            seen.append(host)
    return tuple(seen)


def from_env(env: Mapping[str, str] | None = None) -> Config:
    """Read and validate the contract, or raise :class:`ConfigError`."""
    env = os.environ if env is None else env

    region = (env.get("AWS_REGION") or "").strip()
    cluster = (env.get("ECS_CLUSTER") or "").strip()
    apps_table = (env.get("APPS_TABLE") or "").strip()
    audit_table = (env.get("AUDIT_TABLE") or "").strip()
    log_level = (env.get("LOG_LEVEL") or "").strip().lower() or DEFAULT_LOG_LEVEL
    portal_hosts = parse_portal_hosts(env.get("PORTAL_HOSTS") or "")
    portal_dist = (env.get("PORTAL_DIST") or "").strip() or DEFAULT_PORTAL_DIST

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

    creation, creation_error = creation_from_env(env)
    signout, signout_error = signout_from_env(env, region)
    return Config(
        cluster=cluster,
        apps_table=apps_table,
        audit_table=audit_table,
        region=region,
        port=port,
        log_level=log_level,
        portal_hosts=portal_hosts,
        portal_dist=portal_dist,
        creation=creation,
        creation_error=creation_error,
        signout=signout,
        signout_error=signout_error,
    )


def signout_from_env(env: Mapping[str, str], region: str = "") -> tuple[SignOut | None, str]:
    """The sign-out block: the settings, or ``None`` plus why not.

    Never raises. See :class:`SignOut` for why a missing hosted-UI domain
    degrades sign-out instead of failing startup.
    """
    values = {name: (env.get(name) or "").strip() for name in SIGNOUT_VARIABLES}
    if not any(values.values()):
        return None, ""  # nobody has asked for sign-out on this deployment

    missing = [name for name in SIGNOUT_VARIABLES if not values[name]]
    if missing:
        return None, (
            "sign-out cannot end the Cognito session: partly configured, "
            "missing " + ", ".join(missing)
        )

    domain = values["COGNITO_DOMAIN"]
    if "." not in domain and not region:
        # The prefix form is only half an address. Guessing a region here
        # would produce a plausible URL that Cognito answers with an error
        # page, which is a worse failure than not redirecting at all.
        return None, (
            "sign-out cannot end the Cognito session: COGNITO_DOMAIN "
            f"{domain!r} is a hosted-UI prefix and AWS_REGION is not set"
        )

    return (
        SignOut(
            domain=domain,
            client_id=values["COGNITO_CLIENT_ID"],
            region=region,
            signed_out_url=(env.get("SIGNED_OUT_URL") or "").strip(),
        ),
        "",
    )


def creation_from_env(env: Mapping[str, str]) -> tuple[Creation | None, str]:
    """The P2a block: the settings, or ``None`` plus why not.

    Never raises. See :class:`Creation` for why this one block degrades
    instead of failing startup the way the rest of the contract does.
    """
    values = {name: (env.get(name) or "").strip() for name in CREATION_VARIABLES}
    present = [name for name, value in values.items() if value]
    if not present:
        return None, ""  # the P2a Terraform has not landed yet; creation is off

    missing = [name for name in CREATION_VARIABLES if not values[name]]
    if missing:
        return None, (
            "app creation is disabled: partly configured, missing "
            + ", ".join(missing)
        )

    subnets = tuple(
        chunk.strip() for chunk in values["APP_SUBNET_IDS"].split(",") if chunk.strip()
    )
    if not subnets:
        return None, "app creation is disabled: APP_SUBNET_IDS names no subnets"

    return _creation(values, subnets), ""


def _creation(values: Mapping[str, str], subnets: tuple[str, ...]) -> Creation:
    return Creation(
        uploads_bucket=values["UPLOADS_BUCKET"],
        codebuild_project=values["CODEBUILD_PROJECT"],
        role_boundary_arn=values["APP_ROLE_BOUNDARY_ARN"],
        data_bucket=values["APP_DATA_BUCKET"],
        domain=normalize_host(values["APP_DOMAIN"]),
        subnet_ids=subnets,
        security_group_id=values["APP_SECURITY_GROUP_ID"],
        execution_role_arn=values["APP_EXECUTION_ROLE_ARN"],
        log_group=values["APP_LOG_GROUP"],
        user_pool_id=values["COGNITO_USER_POOL_ID"],
        client_id=values["COGNITO_CLIENT_ID"],
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
