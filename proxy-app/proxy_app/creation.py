"""Validation for self-service app creation. Pure: no AWS, no HTTP, no clock.

The contract is ``docs/design/portal-api.md`` ("P2a additions -- creation")
and the decisions behind it are in ``docs/design/portal-p2a.md``. This module
answers three questions and nothing else:

* is this key allowed to become a public hostname (:func:`check_key`),
* is this upload declaration within the cap (:func:`validate_upload`),
* is this wizard submission a complete, coherent app (:func:`validate_create`).

Everything here is separated from `provision` on purpose. Validation is the
part that has to be exhaustively testable without a single fake AWS client,
and it is the part that decides what goes on the public internet, so it is
where the paranoid tests live.

--- why the key rules are this strict ---------------------------------------

``<key>.tools.stratevi.com`` is visible to clients. A key is not a variable
name: it is a hostname a Stratevi client reads in their address bar, quotes
in an email, and finds in their browser history a year later. So it is
policed in three layers, in this order (portal-p2a.md, "Hostnames are
policed"):

1. **Shape** -- 3-30 chars, lowercase, no leading/trailing/double hyphen.
   Anything else either is not a legal DNS label or looks like a typo.
2. **Reserved names** -- infrastructure words plus every hostname already in
   the table. A "www" or a second "dashboard" would shadow something real.
3. **Denylist substrings** -- brand, molecule and client names, held in
   ``__config__.key_denylist`` so the list is edited without a deploy.

The denylist rejection message NEVER says which term matched. The list is
itself confidential (it is a list of who Stratevi works with and on what),
and a wizard that plays hot-and-cold with it is a disclosure oracle: submit
"pfizer", "novartis", "roche" and read the answers off the form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from . import registry

# --- the key ---------------------------------------------------------------

MIN_KEY_CHARS = 3
MAX_KEY_CHARS = 30

#: A legal DNS label, minus the things that are legal but look like mistakes.
#: Uppercase is not "normalized away" -- a key typed in caps is a person who
#: has not understood that this becomes a hostname, and telling them beats
#: silently lowercasing it and having the URL not match what they typed.
KEY_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

#: Never available, whatever the table says. Infrastructure and convention
#: names that either already resolve or would be actively confusing on this
#: domain. `shinyplatform` and `dashboards` are the portal's own hostnames.
RESERVED_KEYS = frozenset(
    {
        "www",
        "api",
        "auth",
        "admin",
        "proxy",
        "shinyplatform",
        "dashboards",
        "portal",
        "mail",
    }
)

#: The one message a denylisted key ever gets. Deliberately actionable
#: ("pick a project codename") and deliberately silent about why.
DENYLIST_MESSAGE = (
    "that name can't be used in a public hostname -- pick a project codename"
)


def host_for(key: str, domain: str) -> str:
    """``<key>.<domain>``, normalized the way a Host header would arrive."""
    return registry.normalize_host(f"{(key or '').strip()}.{(domain or '').strip()}")


def reserved_labels(hosts: Iterable[str], keys: Iterable[str] = ()) -> frozenset[str]:
    """Everything already taken, as bare labels.

    Both halves matter. A row's ``app_key`` is what its ECR repository and
    IAM role are named after, and a row's HOST LABEL is what actually
    resolves -- an app seeded with a host that does not match its key (the
    migrated ones predate the wizard) blocks both spellings, not one.
    """
    taken: set[str] = set()
    for host in hosts:
        normalized = registry.normalize_host(host)
        if not normalized or registry.is_config_host(normalized):
            continue
        taken.add(normalized.split(".", 1)[0])
    for key in keys:
        cleaned = (key or "").strip().lower()
        if cleaned:
            taken.add(cleaned)
    return frozenset(taken)


def check_key(
    key: Any, *, taken: Iterable[str] = (), denylist: Iterable[str] = ()
) -> str | None:
    """``None`` when the key may be used, otherwise the reason it may not.

    A reason, not an exception: ``POST /apps/validate-key`` answers 200 either
    way (it is a form affordance, not an error) and the create path turns the
    same string into a 400. One implementation, so the wizard's live check and
    the server's real check can never disagree.
    """
    if not isinstance(key, str):
        return "the key must be text"

    cleaned = key.strip()
    if not cleaned:
        return "pick a name for the app's address"
    if cleaned != cleaned.lower():
        return "the key must be lowercase -- it becomes part of a hostname"
    if len(cleaned) < MIN_KEY_CHARS or len(cleaned) > MAX_KEY_CHARS:
        return f"the key must be {MIN_KEY_CHARS}-{MAX_KEY_CHARS} characters"
    if not KEY_PATTERN.match(cleaned):
        return (
            "the key may use lowercase letters, numbers and hyphens, and must "
            "start and end with a letter or number"
        )
    if "--" in cleaned:
        # Legal DNS, but `xn--` is the punycode prefix and a double hyphen in
        # a client-visible name reads as a typo every single time.
        return "the key must not contain two hyphens in a row"

    if cleaned in RESERVED_KEYS:
        return "that name is reserved"
    if cleaned in {(t or "").strip().lower() for t in taken}:
        return "that name is already in use"

    for term in denylist:
        term = (term or "").strip().lower()
        if term and term in cleaned:
            # No echo of `term`. See the module header.
            return DENYLIST_MESSAGE

    return None


# --- the upload ------------------------------------------------------------

#: portal-p2a.md's cap. Checked here, BEFORE a presigned URL exists, so an
#: over-size bundle is refused by a 400 on a JSON call rather than by an S3
#: error the browser reports as an opaque CORS failure.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

MAX_FILENAME_CHARS = 255

#: What a presigned key the API issued looks like. Re-checked on create so a
#: caller cannot substitute an arbitrary object in the uploads bucket for the
#: one they were given a URL for.
UPLOAD_KEY_PATTERN = re.compile(
    r"^uploads/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.zip$"
)


class CreateError(ValueError):
    """A body the contract does not allow. Always a 400, never a 500."""


def validate_upload(body: Any) -> tuple[str, int]:
    """Check an upload declaration and return ``(filename, size)``."""
    if not isinstance(body, dict):
        raise CreateError("body must be a JSON object")

    unknown = sorted(set(body) - {"filename", "size"})
    if unknown:
        raise CreateError("unknown field(s): " + ", ".join(unknown))

    filename = body.get("filename")
    if not isinstance(filename, str) or not filename.strip():
        raise CreateError("filename must be a string")
    filename = filename.strip()
    if len(filename) > MAX_FILENAME_CHARS:
        raise CreateError(f"filename must be at most {MAX_FILENAME_CHARS} characters")
    if not filename.lower().endswith(".zip"):
        raise CreateError("the bundle must be a .zip file")

    size = body.get("size")
    if isinstance(size, bool) or not isinstance(size, int):
        raise CreateError("size must be a whole number of bytes")
    if size <= 0:
        raise CreateError("size must be a whole number of bytes")
    if size > MAX_UPLOAD_BYTES:
        raise CreateError(
            f"the bundle must be at most {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
        )

    return filename, size


# --- the create body -------------------------------------------------------

#: The two Fargate sizes this platform has actually run (CLAUDE.md's cost
#: model): 0.5 vCPU / 2 GB for a dashboard, 4 vCPU / 16 GB for a model.
#: ADR-0001's note applies -- Fargate bills per second, so a bigger task that
#: finishes a CPU-bound run sooner costs about the same. Size up, not down.
TASK_SIZES: tuple[tuple[int, int], ...] = ((512, 2048), (4096, 16384))

MAX_PACKAGES = 300

#: CRAN package names. Letters, digits and dots, starting with a letter --
#: this is R's own rule, and it matters because the list is interpolated into
#: a Dockerfile's `install.packages` call by the build.
PACKAGE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9.]*$")

#: Field order is the audit's order and the review screen's order.
CREATE_FIELDS = (
    "key",
    "label",
    "description",
    "cpu",
    "memory",
    "upload_key",
    "access_mode",
    "allowed_emails",
    "idle_minutes",
    "max_session_hours",
    "expires_at",
    "packages",
)


@dataclass(frozen=True)
class CreateSpec:
    """A validated wizard submission. Everything needed to reserve a row."""

    key: str
    label: str
    description: str
    cpu: int
    memory: int
    upload_key: str
    access_mode: str
    allowed_emails: tuple[str, ...] = field(default_factory=tuple)
    idle_minutes: int = registry.DEFAULT_IDLE_MINUTES
    max_session_hours: int = registry.DEFAULT_MAX_SESSION_HOURS
    expires_at: int = 0
    packages: tuple[str, ...] = field(default_factory=tuple)

    def host(self, domain: str) -> str:
        return host_for(self.key, domain)

    def ecs_service(self, prefix: str = "shiny") -> str:
        return f"{prefix}-{self.key}"

    def repository(self, prefix: str = "shiny") -> str:
        return f"{prefix}-{self.key}"


def validate_create(body: Any) -> CreateSpec:
    """Check a create body against the contract and normalize what survives.

    Shape only: whether the KEY is available needs the table and is
    :func:`check_key`'s job, called by the handler with the rows in hand.

    ``expires_at`` is the one field with no default. ``null`` means never and
    is accepted; ABSENT is a 400. The wizard has an explicit "never" choice
    for exactly this reason (portal-p2a.md's Expiry step) -- an app that
    quietly lives forever because a field was omitted is how a client demo
    becomes permanent infrastructure.
    """
    if not isinstance(body, dict):
        raise CreateError("body must be a JSON object")

    unknown = sorted(set(body) - set(CREATE_FIELDS))
    if unknown:
        raise CreateError("unknown field(s): " + ", ".join(unknown))

    missing = [name for name in CREATE_FIELDS if name not in body]
    if missing:
        raise CreateError("missing field(s): " + ", ".join(missing))

    key = body["key"]
    if not isinstance(key, str):
        raise CreateError("the key must be text")
    key = key.strip()

    label = _text("label", body["label"], limit=200, required=True)
    description = _text("description", body["description"], limit=2000)
    cpu, memory = _task_size(body["cpu"], body["memory"])
    upload_key = _upload_key(body["upload_key"])
    access_mode = _access_mode(body["access_mode"])
    allowed_emails = _allowed_emails(body["allowed_emails"])
    idle_minutes = _whole("idle_minutes", body["idle_minutes"], 1, 1440)
    max_session_hours = _whole("max_session_hours", body["max_session_hours"], 0, 168)
    expires_at = _expires_at(body["expires_at"])
    packages = _packages(body["packages"])

    if access_mode == registry.MODE_USERS and not allowed_emails:
        # The row would be legal and the app unreachable by anybody -- the
        # same state seed.py refuses to write. Catch it here rather than
        # spending fifteen minutes of CodeBuild on it.
        raise CreateError(
            "users mode needs at least one address, or nobody can open the app"
        )

    return CreateSpec(
        key=key,
        label=label,
        description=description,
        cpu=cpu,
        memory=memory,
        upload_key=upload_key,
        access_mode=access_mode,
        allowed_emails=allowed_emails,
        idle_minutes=idle_minutes,
        max_session_hours=max_session_hours,
        expires_at=expires_at,
        packages=packages,
    )


def cpu_workers(cpu: int) -> int:
    """``SHINY_CPU_WORKERS`` for a task of this size (ADR-0011).

    One core left for Shiny's own event loop, and never below 1: an app that
    reads a 0 here would run nothing at all.
    """
    return max(1, cpu // 1024 - 1)


def _text(name: str, value: Any, *, limit: int, required: bool = False) -> str:
    if not isinstance(value, str):
        raise CreateError(f"{name} must be a string")
    text = value.strip()
    if required and not text:
        raise CreateError(f"{name} is required")
    if len(text) > limit:
        raise CreateError(f"{name} must be at most {limit} characters")
    return text


def _task_size(cpu: Any, memory: Any) -> tuple[int, int]:
    if isinstance(cpu, bool) or not isinstance(cpu, int):
        raise CreateError("cpu must be a whole number")
    if isinstance(memory, bool) or not isinstance(memory, int):
        raise CreateError("memory must be a whole number")
    if (cpu, memory) not in TASK_SIZES:
        allowed = " or ".join(f"{c}/{m}" for c, m in TASK_SIZES)
        raise CreateError(f"cpu/memory must be one of {allowed}")
    return cpu, memory


def _upload_key(value: Any) -> str:
    if not isinstance(value, str):
        raise CreateError("upload_key must be a string")
    key = value.strip()
    if not UPLOAD_KEY_PATTERN.match(key):
        # Not cosmetic: this is the only thing stopping a caller pointing the
        # build at some other object in the uploads bucket.
        raise CreateError("upload_key is not one this service issued")
    return key


def _access_mode(value: Any) -> str:
    if not isinstance(value, str):
        raise CreateError("access_mode must be a string")
    mode = value.strip().lower()
    if mode in registry.RESERVED_MODES:
        raise CreateError(
            f"access_mode {mode} is reserved for a later phase and the proxy "
            "refuses every request to an app carrying it"
        )
    if mode not in registry.IMPLEMENTED_MODES:
        raise CreateError("access_mode must be all_users or users")
    return mode


def _allowed_emails(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise CreateError("allowed_emails must be a list")
    if len(value) > 500:
        raise CreateError("allowed_emails must hold at most 500 addresses")

    cleaned: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise CreateError("allowed_emails must be a list of strings")
        address = entry.strip().lower()
        if not address:
            continue
        if "@" not in address:
            raise CreateError(f"{entry!r} is not an email address")
        if address not in cleaned:
            cleaned.append(address)
    return tuple(cleaned)


def _whole(name: str, value: Any, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CreateError(f"{name} must be a whole number")
    if not low <= value <= high:
        raise CreateError(f"{name} must be between {low} and {high}")
    return value


def _expires_at(value: Any) -> int:
    if value is None:
        return 0  # an explicit "never" -- absence was rejected above
    if isinstance(value, bool) or not isinstance(value, int):
        raise CreateError("expires_at must be epoch seconds or null")
    if value < 0:
        raise CreateError("expires_at must be epoch seconds or null")
    return value


def _packages(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise CreateError("packages must be a list")
    if len(value) > MAX_PACKAGES:
        raise CreateError(f"packages must hold at most {MAX_PACKAGES} names")

    cleaned: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise CreateError("packages must be a list of strings")
        name = entry.strip()
        if not name:
            continue
        if not PACKAGE_PATTERN.match(name):
            raise CreateError(f"{entry!r} is not an R package name")
        if name not in cleaned:
            cleaned.append(name)
    return tuple(cleaned)


def package_list(packages: Sequence[str]) -> str:
    """The build's ``PACKAGES`` override: space separated, in wizard order.

    Space separated because that is what ``buildspec/Dockerfile.template``'s
    ``__PACKAGES__`` token expects (its header documents the contract).
    """
    return " ".join(packages)


__all__ = [
    "CreateError",
    "CreateSpec",
    "DENYLIST_MESSAGE",
    "KEY_PATTERN",
    "MAX_KEY_CHARS",
    "MAX_UPLOAD_BYTES",
    "MIN_KEY_CHARS",
    "RESERVED_KEYS",
    "TASK_SIZES",
    "UPLOAD_KEY_PATTERN",
    "check_key",
    "cpu_workers",
    "host_for",
    "package_list",
    "reserved_labels",
    "validate_create",
    "validate_upload",
]
