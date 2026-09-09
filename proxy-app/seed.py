"""Write the repo-root ``catalog.yaml`` into the shiny-proxy-apps table.

The migration-time tool from the design spec, not a supported control plane:
the portal phase replaces it. Run it by hand when an app's host moves behind
the proxy, and check ``--dry-run`` first -- these rows are the access control
for every app the proxy fronts.

``--table`` is required with no default on purpose. There is exactly one
production table and one obvious name for it, which is precisely why typing it
should be a deliberate act.

    python seed.py --table shiny-proxy-apps --dry-run
    python seed.py --table shiny-proxy-apps --only model
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit

import yaml

from proxy_app import registry
from proxy_app.registry import App

#: The Terraform project prefix; an app's service defaults to <prefix>-<key>.
DEFAULT_SERVICE_PREFIX = "shiny"


class SeedError(RuntimeError):
    """A catalog the proxy cannot safely turn into rows."""


@dataclass(frozen=True)
class Options:
    """The knobs that change how a catalog entry becomes a row."""

    service_prefix: str = DEFAULT_SERVICE_PREFIX
    container_port: int = registry.DEFAULT_CONTAINER_PORT
    idle_minutes: int = registry.DEFAULT_IDLE_MINUTES


# --- parsing (pure; this is what the tests drive) --------------------------


def host_from_url(url: str) -> str:
    """The hostname a catalog tile links to.

    ``catalog.yaml`` has no ``host`` field -- the portal only ever needed the
    ``url`` -- so the proxy's partition key is derived from it. A bare
    "model.tools.stratevi.com" with no scheme is accepted too, because someone
    will eventually write one.
    """
    text = (url or "").strip()
    if not text:
        return ""
    if "//" not in text:
        text = "//" + text
    return registry.normalize_host(urlsplit(text).hostname or "")


def app_from_entry(entry: dict[str, Any], options: Options = Options()) -> App:
    """Turn one ``catalog.yaml`` entry into a validated app row."""
    if not isinstance(entry, dict):
        raise SeedError(f"catalog entry is not a mapping: {entry!r}")

    key = str(entry.get("key") or "").strip()
    if not key:
        raise SeedError("catalog entry with no key")

    host = registry.normalize_host(str(entry.get("host") or "")) or host_from_url(
        str(entry.get("url") or "")
    )
    if not host:
        raise SeedError(
            f"app {key!r}: cannot read a host from url {entry.get('url')!r}"
        )

    app = App.create(
        host=host,
        app_key=key,
        ecs_service=str(entry.get("ecs_service") or "").strip()
        or f"{options.service_prefix}-{key}",
        container_port=entry.get("container_port") or options.container_port,
        status=str(entry.get("status") or ""),
        access_mode=str(entry.get("access_mode") or ""),
        allowed_emails=entry.get("allowed_emails") or (),
        idle_minutes=entry.get("idle_minutes") or options.idle_minutes,
        expires_at=entry.get("expires_at") or 0,
        # No CLI default, unlike idle_minutes/container_port: the only sane
        # default is uncapped (0), which App.create already applies when the
        # catalog entry omits the key entirely.
        max_session_hours=entry.get("max_session_hours") or 0,
    )

    if app.access_mode not in registry.IMPLEMENTED_MODES:
        raise SeedError(
            f"app {key!r}: access_mode {app.access_mode!r} is reserved or unknown, "
            "and the proxy refuses every request to an app carrying it"
        )
    if app.access_mode == registry.MODE_USERS and not app.allowed_emails:
        raise SeedError(
            f"app {key!r}: access_mode users with an empty allowed_emails would "
            "lock everyone out"
        )

    return app


def apps_from_catalog(
    document: dict[str, Any], options: Options = Options(), only: str = ""
) -> list[App]:
    """Turn a parsed ``catalog.yaml`` document into rows."""
    entries = (document or {}).get("apps") or []
    if not isinstance(entries, list):
        raise SeedError("catalog: `apps` is not a list")

    rows = [
        app_from_entry(entry, options)
        for entry in entries
        if not only or (isinstance(entry, dict) and entry.get("key") == only)
    ]
    if not rows:
        raise SeedError(
            f"no apps to seed (only={only!r})" if only else "no apps to seed"
        )
    return rows


def load_catalog(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise SeedError(f"{path}: not a YAML mapping")
    return document


def resolve_catalog(given: str | None, start: Path | None = None) -> Path:
    """Find the repo-root catalog.yaml.

    The tool is run from ``proxy-app/`` as often as from the repo root, so walk
    up rather than making the caller count "../"s.
    """
    if given:
        return Path(given)

    directory = (start or Path.cwd()).resolve()
    for candidate in [directory, *directory.parents][:6]:
        found = candidate / "catalog.yaml"
        if found.is_file():
            return found
    raise SeedError(
        "no catalog.yaml at or above the working directory; pass --catalog"
    )


def render_items(apps: Iterable[App]) -> str:
    """Exactly what a real run would PutItem, in the AWS CLI's wire shape."""
    return "\n".join(
        json.dumps(registry.app_item(app), indent=2, sort_keys=True) for app in apps
    )


# --- CLI -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seed.py",
        description="Seed shiny-proxy-apps from the repo-root catalog.yaml.",
    )
    parser.add_argument(
        "--table",
        required=True,
        help="DynamoDB apps table to write (required, e.g. shiny-proxy-apps)",
    )
    parser.add_argument(
        "--catalog",
        default="",
        help="path to catalog.yaml (default: the nearest one at or above the cwd)",
    )
    parser.add_argument("--region", default="", help="AWS region (default: SDK resolution)")
    parser.add_argument(
        "--service-prefix",
        default=DEFAULT_SERVICE_PREFIX,
        help="Terraform project prefix; ecs_service becomes <prefix>-<key>",
    )
    parser.add_argument(
        "--container-port",
        type=int,
        default=registry.DEFAULT_CONTAINER_PORT,
        help="container port when the catalog does not say",
    )
    parser.add_argument(
        "--idle-minutes",
        type=int,
        default=registry.DEFAULT_IDLE_MINUTES,
        help="idle minutes when the catalog does not say",
    )
    parser.add_argument("--only", default="", help="seed just this app key")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the items instead of writing them",
    )
    return parser


async def _write(table: str, region: str, apps: Sequence[App]) -> None:
    import boto3  # imported late so --dry-run needs no credentials at all

    session = boto3.session.Session(**({"region_name": region} if region else {}))
    store = registry.DynamoAppStore(session.client("dynamodb"), table)
    for app in apps:
        await store.put(app)
        print(f"wrote {app.host} -> {app.ecs_service} ({app.status}, {app.access_mode})")
    print(f"{len(apps)} item(s) written to {table}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        path = resolve_catalog(args.catalog)
        apps = apps_from_catalog(
            load_catalog(path),
            Options(
                service_prefix=args.service_prefix,
                container_port=args.container_port,
                idle_minutes=args.idle_minutes,
            ),
            only=args.only,
        )
    except (SeedError, OSError, yaml.YAMLError) as exc:
        print(f"seed: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"# dry run: {len(apps)} item(s) from {path} for table {args.table}")
        print(render_items(apps))
        return 0

    try:
        asyncio.run(_write(args.table, args.region, apps))
    except Exception as exc:
        print(f"seed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
