"""Seed parsing, driven against the real repo-root catalog.yaml.

The catalog is the file a human edits at migration time, so the shape these
tests assert is the shape that actually exists two directories up -- not a
fixture that can drift away from it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import seed
from proxy_app import registry

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG = REPO_ROOT / "catalog.yaml"


@pytest.fixture(scope="module")
def catalog() -> dict:
    return seed.load_catalog(CATALOG)


# --- the real catalog ------------------------------------------------------


def test_the_repo_catalog_is_where_the_tool_expects_it():
    assert CATALOG.is_file(), f"expected the repo-root catalog at {CATALOG}"
    assert seed.resolve_catalog(None, start=Path(__file__).parent) == CATALOG


def test_the_real_catalog_seeds_cleanly(catalog):
    apps = seed.apps_from_catalog(catalog)
    by_key = {app.app_key: app for app in apps}

    assert set(by_key) == {"dashboard", "model"}

    model = by_key["model"]
    assert model.host == "model.tools.stratevi.com"
    assert model.ecs_service == "shiny-model"  # <prefix>-<key>
    assert model.container_port == registry.DEFAULT_CONTAINER_PORT
    assert model.idle_minutes == registry.DEFAULT_IDLE_MINUTES
    assert model.status == registry.STATUS_ACTIVE
    assert model.access_mode == registry.MODE_USERS
    assert "jake@stratevi.com" in model.allowed_emails
    # The Entra-federated alias for the same person must survive the parse --
    # dropping it refuses that sign-in path (see catalog.yaml's own comment).
    assert "jake.pistotnik@assembledintelligence.co.uk" in model.allowed_emails
    assert model.expires_at == 0

    dashboard = by_key["dashboard"]
    assert dashboard.host == "dashboard.tools.stratevi.com"
    assert dashboard.ecs_service == "shiny-dashboard"
    assert len(dashboard.allowed_emails) == 8


def test_only_selects_a_single_app(catalog):
    apps = seed.apps_from_catalog(catalog, only="model")
    assert [app.app_key for app in apps] == ["model"]


def test_only_with_an_unknown_key_is_an_error_not_an_empty_write(catalog):
    with pytest.raises(seed.SeedError):
        seed.apps_from_catalog(catalog, only="does-not-exist")


def test_dry_run_renders_the_wire_shape_of_every_item(catalog):
    rendered = seed.render_items(seed.apps_from_catalog(catalog, only="model"))
    assert '"S": "model.tools.stratevi.com"' in rendered
    assert '"N": "3838"' in rendered
    assert '"SS"' in rendered


def test_the_migrate_model_first_case_is_a_single_deliberate_item(catalog):
    """The design spec migrates `model` first -- it is currently a dead link."""
    apps = seed.apps_from_catalog(catalog, only="model")
    item = registry.app_item(apps[0])
    assert item["host"] == {"S": "model.tools.stratevi.com"}
    assert item["ecs_service"] == {"S": "shiny-model"}
    assert item["status"] == {"S": "active"}
    assert item["access_mode"] == {"S": "users"}


# --- host derivation -------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://model.tools.stratevi.com", "model.tools.stratevi.com"),
        ("https://Model.Tools.Stratevi.com/", "model.tools.stratevi.com"),
        ("https://model.tools.stratevi.com:443/path?q=1", "model.tools.stratevi.com"),
        ("http://model.tools.stratevi.com", "model.tools.stratevi.com"),
        ("model.tools.stratevi.com", "model.tools.stratevi.com"),
        ("", ""),
    ],
)
def test_host_from_url(url, expected):
    assert seed.host_from_url(url) == expected


def test_an_explicit_host_field_wins_over_the_url():
    app = seed.app_from_entry(
        {
            "key": "model",
            "url": "https://old.tools.stratevi.com",
            "host": "NEW.tools.stratevi.com",
            "access_mode": "all_users",
        }
    )
    assert app.host == "new.tools.stratevi.com"


# --- validation: the seed refuses what the proxy would refuse --------------


def test_an_entry_with_no_key_is_refused():
    with pytest.raises(seed.SeedError, match="no key"):
        seed.app_from_entry({"url": "https://a.tools.stratevi.com"})


def test_an_entry_with_no_readable_host_is_refused():
    with pytest.raises(seed.SeedError, match="cannot read a host"):
        seed.app_from_entry({"key": "model"})


@pytest.mark.parametrize(
    "mode", ["team", "organizations", "client_magic_link", "everyone_lol"]
)
def test_reserved_and_unknown_access_modes_are_refused(mode):
    with pytest.raises(seed.SeedError, match="reserved or unknown"):
        seed.app_from_entry(
            {
                "key": "model",
                "url": "https://model.tools.stratevi.com",
                "access_mode": mode,
                "allowed_emails": ["jake@stratevi.com"],
            }
        )


def test_users_mode_with_an_empty_list_is_refused_rather_than_locking_everyone_out():
    with pytest.raises(seed.SeedError, match="lock everyone out"):
        seed.app_from_entry(
            {"key": "model", "url": "https://model.tools.stratevi.com", "access_mode": "users"}
        )


def test_all_users_needs_no_allowed_emails():
    app = seed.app_from_entry(
        {
            "key": "portal",
            "url": "https://dashboards.tools.stratevi.com",
            "access_mode": "all_users",
        }
    )
    assert app.access_mode == registry.MODE_ALL_USERS
    assert app.allowed_emails == ()


def test_a_catalog_omitting_access_mode_defaults_to_users_and_must_list_emails():
    with pytest.raises(seed.SeedError, match="lock everyone out"):
        seed.app_from_entry({"key": "model", "url": "https://model.tools.stratevi.com"})


# --- overrides -------------------------------------------------------------


def test_per_entry_overrides_beat_the_defaults():
    app = seed.app_from_entry(
        {
            "key": "model",
            "url": "https://model.tools.stratevi.com",
            "access_mode": "all_users",
            "ecs_service": "shiny-model-v2",
            "container_port": 8080,
            "idle_minutes": 45,
            "expires_at": 1_800_000_000,
            "status": "disabled",
        }
    )
    assert app.ecs_service == "shiny-model-v2"
    assert app.container_port == 8080
    assert app.idle_minutes == 45
    assert app.expires_at == 1_800_000_000
    assert app.status == registry.STATUS_DISABLED


def test_the_service_prefix_option_matches_the_terraform_project_prefix():
    app = seed.app_from_entry(
        {"key": "model", "url": "https://model.tools.stratevi.com", "access_mode": "all_users"},
        seed.Options(service_prefix="shiny"),
    )
    assert app.ecs_service == "shiny-model"


# --- CLI surface -----------------------------------------------------------


def test_table_is_required():
    with pytest.raises(SystemExit):
        seed.build_parser().parse_args(["--dry-run"])


def test_dry_run_writes_nothing_and_exits_zero(capsys):
    code = seed.main(["--table", "shiny-proxy-apps", "--catalog", str(CATALOG), "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "dry run: 2 item(s)" in out
    assert "shiny-proxy-apps" in out


def test_a_catalog_entry_the_proxy_could_not_serve_is_refused():
    with pytest.raises(seed.SeedError, match="cannot read a host"):
        seed.apps_from_catalog({"apps": [{"key": "model"}]})


def test_a_catalog_whose_apps_key_is_not_a_list_is_refused():
    with pytest.raises(seed.SeedError, match="not a list"):
        seed.apps_from_catalog({"apps": "model"})


def test_an_empty_catalog_writes_nothing_rather_than_succeeding_silently():
    with pytest.raises(seed.SeedError, match="no apps to seed"):
        seed.apps_from_catalog({"apps": []})


def test_a_missing_catalog_file_is_reported_not_raised(capsys):
    code = seed.main(
        ["--table", "t", "--catalog", str(REPO_ROOT / "no-such-catalog.yaml"), "--dry-run"]
    )
    assert code == 1
    assert "seed:" in capsys.readouterr().err


def test_the_catalog_yaml_the_portal_reads_and_the_seed_reads_are_the_same_file(catalog):
    """catalog.yaml drives both the portal menu and these rows (ADR-0013)."""
    raw = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    assert raw == catalog
    for entry in raw["apps"]:
        assert {"key", "label", "description", "url", "access_mode"} <= set(entry)
