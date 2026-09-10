"""Seed parsing, driven against a synthetic catalog written to a temp dir.

These tests used to read the repo-root ``catalog.yaml``. That file is gone:
ADR-0013's Lambda portal was retired and the catalog it fed went with it (see
``docs/STATUS.md``), so a suite that reads it fails at collection time.

``seed.py`` itself stays and is unchanged. It takes ``--catalog``, it still
has to turn a catalog into rows correctly, and it fails loudly with a legible
message when the file is absent -- which is the behaviour a migration tool
should have. What changed is only where the tests get their input.

:data:`CATALOG_DOCUMENT` deliberately mirrors the shape of the retired file --
two apps, ``users`` mode, the Entra-federated aliases, labels and
descriptions, no ``max_session_hours`` -- because that shape is what the
parser was written against and the coverage is worth keeping. It is a fixture
now rather than a fact about the repository.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

import seed
from proxy_app import registry

#: The retired repo-root catalog's structure, as a fixture. Two apps, both in
#: `users` mode, each carrying the Microsoft365/Entra alias of the same person
#: alongside the native Cognito address -- Cognito hands back a different
#: email per identity provider, and dropping either one refuses that sign-in
#: path.
CATALOG_DOCUMENT = {
    "apps": [
        {
            "key": "dashboard",
            "label": "Treatment Pathway Dashboard",
            "description": (
                "Sankey diagram of treatment sequences after 2022 Tarpeyo "
                "initiation."
            ),
            "url": "https://dashboard.tools.stratevi.com",
            "access_mode": "users",
            "allowed_emails": [
                "jake@stratevi.com",
                "jake.pistotnik@assembledintelligence.co.uk",
                "nick@stratevi.com",
                "nick.adair@assembledintelligence.co.uk",
                "yi@stratevi.com",
                "yi.pan@assembledintelligence.co.uk",
                "josh@stratevi.com",
                "josh.epstein@assembledintelligence.co.uk",
            ],
        },
        {
            "key": "model",
            "label": "Microsimulation Model",
            "description": "Patient-level microsimulation across four treatment passes.",
            "url": "https://model.tools.stratevi.com",
            "access_mode": "users",
            "allowed_emails": [
                "jake@stratevi.com",
                "jake.pistotnik@assembledintelligence.co.uk",
            ],
        },
    ]
}


@pytest.fixture(scope="module")
def workspace():
    """A scratch directory.

    ``tempfile`` rather than pytest's ``tmp_path``, for the same reason
    ``test_portal.py`` says: this repo's Windows machine has an unreadable
    ``pytest-of-<user>`` left in %TEMP% and the tmp_path fixture cannot get
    past it.
    """
    with tempfile.TemporaryDirectory() as directory:
        # .resolve(): %TEMP% on this machine is an 8.3 short path
        # ("JAKEPI~1"), and resolve_catalog resolves what it returns, so
        # comparing an unresolved path against it fails on spelling alone.
        yield Path(directory).resolve()


@pytest.fixture(scope="module")
def catalog_file(workspace) -> Path:
    """CATALOG_DOCUMENT on disk, as YAML, exactly as a human would edit it."""
    path = workspace / "catalog.yaml"
    path.write_text(
        yaml.safe_dump(CATALOG_DOCUMENT, sort_keys=False), encoding="utf-8"
    )
    return path


@pytest.fixture(scope="module")
def catalog(catalog_file) -> dict:
    return seed.load_catalog(catalog_file)


# --- a whole catalog -------------------------------------------------------


def test_a_catalog_round_trips_through_the_loader(catalog):
    """load_catalog reads YAML off disk, so parse it rather than passing the
    dict straight in -- a YAML-shaped bug would otherwise never show."""
    assert catalog == CATALOG_DOCUMENT


def test_a_two_app_catalog_seeds_cleanly(catalog):
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
    # dropping it refuses that sign-in path.
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


def test_a_single_app_migration_is_one_deliberate_item(catalog):
    """Apps move behind the proxy one at a time, so `--only` has to produce
    exactly one complete row and nothing else."""
    apps = seed.apps_from_catalog(catalog, only="model")
    item = registry.app_item(apps[0])
    assert item["host"] == {"S": "model.tools.stratevi.com"}
    assert item["ecs_service"] == {"S": "shiny-model"}
    assert item["status"] == {"S": "active"}
    assert item["access_mode"] == {"S": "users"}


# --- finding the file ------------------------------------------------------


def test_an_explicit_catalog_path_is_used_as_given(workspace):
    given = workspace / "elsewhere.yaml"
    assert seed.resolve_catalog(str(given)) == given


def test_the_nearest_catalog_at_or_above_the_directory_is_found(catalog_file):
    """The tool is run from proxy-app/ as often as from a repo root, so it
    walks up rather than making the caller count '../'s."""
    nested = catalog_file.parent / "a" / "b"
    nested.mkdir(parents=True, exist_ok=True)
    assert seed.resolve_catalog(None, start=nested) == catalog_file
    assert seed.resolve_catalog("", start=catalog_file.parent) == catalog_file


def test_no_catalog_anywhere_is_a_legible_error():
    """Its own temp tree: `workspace` has a catalog.yaml at its root, which
    the walk-up would find."""
    with tempfile.TemporaryDirectory() as directory:
        with pytest.raises(seed.SeedError, match="pass --catalog"):
            seed.resolve_catalog(None, start=Path(directory))


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


def test_max_session_hours_is_passed_through_when_the_catalog_sets_it():
    app = seed.app_from_entry(
        {
            "key": "model",
            "url": "https://model.tools.stratevi.com",
            "access_mode": "all_users",
            "max_session_hours": 8,
        }
    )
    assert app.max_session_hours == 8
    assert app.has_session_cap() is True


def test_max_session_hours_defaults_to_uncapped_when_the_catalog_omits_it():
    app = seed.app_from_entry(
        {
            "key": "model",
            "url": "https://model.tools.stratevi.com",
            "access_mode": "all_users",
        }
    )
    assert app.max_session_hours == registry.DEFAULT_MAX_SESSION_HOURS
    assert app.has_session_cap() is False


def test_dry_run_shows_max_session_hours_when_the_catalog_sets_it():
    rendered = seed.render_items(
        [
            seed.app_from_entry(
                {
                    "key": "model",
                    "url": "https://model.tools.stratevi.com",
                    "access_mode": "all_users",
                    "max_session_hours": 8,
                }
            )
        ]
    )
    assert '"max_session_hours"' in rendered
    assert '"N": "8"' in rendered


def test_a_catalog_with_no_session_cap_omits_the_attribute_entirely(catalog):
    """Neither app sets max_session_hours -- confirms the key stays truly
    optional rather than silently defaulting to something nonzero."""
    rendered = seed.render_items(seed.apps_from_catalog(catalog))
    assert "max_session_hours" not in rendered


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


def test_dry_run_writes_nothing_and_exits_zero(capsys, catalog_file):
    code = seed.main(
        ["--table", "shiny-proxy-apps", "--catalog", str(catalog_file), "--dry-run"]
    )
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


def test_a_missing_catalog_file_is_reported_not_raised(capsys, workspace):
    """The repo-root catalog.yaml is gone (ADR-0013's portal was retired), so
    this is now the ordinary case rather than an edge one: the tool must say
    so on stderr and exit 1, not traceback."""
    code = seed.main(
        [
            "--table",
            "t",
            "--catalog",
            str(workspace / "no-such-catalog.yaml"),
            "--dry-run",
        ]
    )
    assert code == 1
    assert "seed:" in capsys.readouterr().err


# --- label and description (the portal's menu reads these off the row) -----


def test_a_catalog_carries_its_labels_and_descriptions_onto_the_rows(catalog):
    """ADR-0014 retires catalog.yaml, so the migration has to carry the tile
    text across or every menu entry loses its name the day the Lambda portal
    is switched off."""
    by_key = {app.app_key: app for app in seed.apps_from_catalog(catalog)}
    assert by_key["dashboard"].label == "Treatment Pathway Dashboard"
    assert by_key["dashboard"].description.startswith("Sankey diagram")
    assert by_key["model"].label == "Microsimulation Model"
    assert by_key["model"].description.startswith("Patient-level microsimulation")


def test_dry_run_shows_the_label_and_description_attributes(catalog):
    rendered = seed.render_items(seed.apps_from_catalog(catalog, only="model"))
    assert '"label"' in rendered
    assert '"S": "Microsimulation Model"' in rendered
    assert '"description"' in rendered


def test_an_entry_with_no_label_seeds_without_the_attribute():
    app = seed.app_from_entry(
        {
            "key": "model",
            "url": "https://model.tools.stratevi.com",
            "access_mode": "all_users",
        }
    )
    assert app.label == ""
    assert "label" not in registry.app_item(app)
