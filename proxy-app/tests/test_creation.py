"""P2a validation: the key rules, the upload cap, the wizard body.

Pure functions, so these are plain tests with no fakes at all. The contract
they encode is ``docs/design/portal-api.md`` ("P2a additions -- creation")
and the decisions in ``docs/design/portal-p2a.md``; if one of them has to
change, those files change first.
"""

from __future__ import annotations

import pytest

from proxy_app import config as config_mod
from proxy_app import creation, registry

DOMAIN = "tools.stratevi.com"
UPLOAD = "uploads/3f2504e0-4f89-11d3-9a0c-0305e82c3301.zip"


def good_body(**overrides) -> dict:
    body = {
        "key": "tarpeyo-uptake",
        "label": "Tarpeyo Uptake",
        "description": "Uptake curves.",
        "cpu": 512,
        "memory": 2048,
        "upload_key": UPLOAD,
        "access_mode": "users",
        "allowed_emails": ["jake@stratevi.com"],
        "idle_minutes": 20,
        "max_session_hours": 12,
        "expires_at": None,
        "packages": ["shiny", "ggplot2"],
    }
    body.update(overrides)
    return body


# --- the key: shape --------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["abc", "a1b", "model-two", "x" * 30, "tarpeyo-uptake", "a-b-c-1", "9lives"],
)
def test_the_keys_the_rules_allow(key):
    assert creation.check_key(key) is None


@pytest.mark.parametrize(
    "key,fragment",
    [
        ("ab", "3-30 characters"),
        ("x" * 31, "3-30 characters"),
        ("", "pick a name"),
        ("   ", "pick a name"),
        ("Model", "lowercase"),
        ("MODEL", "lowercase"),
        ("-model", "start and end"),
        ("model-", "start and end"),
        ("mo del", "lowercase letters, numbers and hyphens"),
        ("mo_del", "lowercase letters, numbers and hyphens"),
        ("mo.del", "lowercase letters, numbers and hyphens"),
        ("model!", "lowercase letters, numbers and hyphens"),
        ("mo--del", "two hyphens in a row"),
        ("xn--80ak6aa92e", "two hyphens in a row"),
        (7, "must be text"),
        (None, "must be text"),
        (["model"], "must be text"),
    ],
)
def test_the_keys_the_rules_refuse(key, fragment):
    reason = creation.check_key(key)
    assert reason is not None and fragment in reason


# --- the key: reserved and taken -------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["www", "api", "auth", "admin", "proxy", "shinyplatform", "dashboards",
     "portal", "mail"],
)
def test_the_reserved_names_are_never_available(key):
    assert creation.check_key(key) == "that name is reserved"


def test_an_existing_row_blocks_its_key_and_its_host_label():
    taken = creation.reserved_labels(
        ["model.tools.stratevi.com", "legacy-host.tools.stratevi.com"],
        ["model", "renamed"],
    )
    assert creation.check_key("model", taken=taken) == "that name is already in use"
    assert creation.check_key("legacy-host", taken=taken) == "that name is already in use"
    # An app whose row host does not match its key blocks BOTH spellings.
    assert creation.check_key("renamed", taken=taken) == "that name is already in use"
    assert creation.check_key("brand-new", taken=taken) is None


def test_config_rows_are_not_treated_as_taken_hostnames():
    taken = creation.reserved_labels([registry.CONFIG_HOST, "model.tools.stratevi.com"])
    assert "__config__" not in taken
    assert taken == frozenset({"model"})


# --- the key: the denylist -------------------------------------------------


def test_a_denylisted_substring_is_refused_anywhere_in_the_key():
    for key in ("tarpeyo", "tarpeyo-uptake", "uptake-tarpeyo", "pre-tarpeyo-post"):
        assert creation.check_key(key, denylist=["tarpeyo"]) == creation.DENYLIST_MESSAGE


def test_the_denylist_rejection_never_says_which_term_matched():
    """The list is who Stratevi works with and on what. A wizard that plays
    hot-and-cold with it is a disclosure oracle."""
    reason = creation.check_key("acme-tarpeyo-x", denylist=["tarpeyo", "acme"])
    assert reason == creation.DENYLIST_MESSAGE
    assert "tarpeyo" not in reason
    assert "acme" not in reason


def test_the_denylist_is_matched_case_insensitively_and_ignores_blanks():
    assert creation.check_key("nefecon-x", denylist=["  NEFECON ", "", None]) == (
        creation.DENYLIST_MESSAGE
    )


def test_a_key_that_merely_resembles_a_denylisted_term_is_fine():
    assert creation.check_key("uptake-model", denylist=["tarpeyo"]) is None


def test_shape_is_checked_before_the_denylist():
    """A bad shape must not be reported as a denylist hit, or every typo
    reads as "you named a client"."""
    reason = creation.check_key("TARPEYO", denylist=["tarpeyo"])
    assert reason is not None and "lowercase" in reason


# --- the host --------------------------------------------------------------


def test_the_host_is_the_key_under_the_platform_domain():
    assert creation.host_for("tarpeyo-uptake", DOMAIN) == (
        "tarpeyo-uptake.tools.stratevi.com"
    )
    assert creation.host_for(" tarpeyo ", "Tools.Stratevi.COM") == (
        "tarpeyo.tools.stratevi.com"
    )


# --- uploads ---------------------------------------------------------------


def test_a_reasonable_upload_declaration_passes():
    assert creation.validate_upload({"filename": "app.zip", "size": 1234}) == (
        "app.zip",
        1234,
    )


@pytest.mark.parametrize(
    "body,fragment",
    [
        ({"filename": "app.zip", "size": creation.MAX_UPLOAD_BYTES + 1}, "at most 100 MB"),
        ({"filename": "app.zip", "size": 0}, "whole number of bytes"),
        ({"filename": "app.zip", "size": -1}, "whole number of bytes"),
        ({"filename": "app.zip", "size": "big"}, "whole number of bytes"),
        ({"filename": "app.zip", "size": 1.5}, "whole number of bytes"),
        ({"filename": "app.zip", "size": True}, "whole number of bytes"),
        ({"filename": "app.tar.gz", "size": 10}, "must be a .zip"),
        ({"filename": "", "size": 10}, "filename must be a string"),
        ({"filename": 7, "size": 10}, "filename must be a string"),
        ({"filename": "x" * 300 + ".zip", "size": 10}, "at most 255"),
        ({"size": 10}, "filename must be a string"),
        ({"filename": "app.zip"}, "size must be a whole number"),
        ({"filename": "app.zip", "size": 10, "bucket": "mine"}, "unknown field"),
        ([], "JSON object"),
        ("nope", "JSON object"),
    ],
)
def test_the_upload_declarations_the_cap_refuses(body, fragment):
    with pytest.raises(creation.CreateError, match=fragment):
        creation.validate_upload(body)


def test_the_cap_is_exactly_one_hundred_megabytes():
    assert creation.MAX_UPLOAD_BYTES == 100 * 1024 * 1024
    assert creation.validate_upload(
        {"filename": "app.zip", "size": creation.MAX_UPLOAD_BYTES}
    )[1] == creation.MAX_UPLOAD_BYTES


# --- the create body -------------------------------------------------------


def test_a_complete_wizard_submission_normalizes():
    spec = creation.validate_create(
        good_body(
            label="  Tarpeyo Uptake  ",
            allowed_emails=[" JAKE@Stratevi.com ", "jake@stratevi.com", ""],
            packages=["shiny", " ggplot2 ", "shiny"],
        )
    )
    assert spec.key == "tarpeyo-uptake"
    assert spec.label == "Tarpeyo Uptake"
    assert spec.allowed_emails == ("jake@stratevi.com",)
    assert spec.packages == ("shiny", "ggplot2")
    assert spec.expires_at == 0
    assert spec.host(DOMAIN) == "tarpeyo-uptake.tools.stratevi.com"
    assert spec.ecs_service() == "shiny-tarpeyo-uptake"
    assert spec.repository() == "shiny-tarpeyo-uptake"


def test_expires_at_must_be_present_even_when_it_is_null():
    """portal-p2a.md: the wizard has an explicit "never". An app that lives
    forever because a field was omitted is how a demo becomes permanent."""
    body = good_body()
    del body["expires_at"]
    with pytest.raises(creation.CreateError, match="missing field.*expires_at"):
        creation.validate_create(body)

    assert creation.validate_create(good_body(expires_at=None)).expires_at == 0
    assert creation.validate_create(good_body(expires_at=1_900_000_000)).expires_at == (
        1_900_000_000
    )


@pytest.mark.parametrize("field", creation.CREATE_FIELDS)
def test_every_field_is_required(field):
    body = good_body()
    del body[field]
    with pytest.raises(creation.CreateError, match="missing field"):
        creation.validate_create(body)


@pytest.mark.parametrize("cpu,memory", creation.TASK_SIZES)
def test_both_known_good_task_sizes_are_accepted(cpu, memory):
    spec = creation.validate_create(good_body(cpu=cpu, memory=memory))
    assert (spec.cpu, spec.memory) == (cpu, memory)


@pytest.mark.parametrize(
    "cpu,memory",
    [(512, 1024), (1024, 2048), (4096, 8192), (256, 512), (512, 16384), (0, 0)],
)
def test_an_unproven_task_size_is_refused(cpu, memory):
    with pytest.raises(creation.CreateError, match="cpu/memory must be one of"):
        creation.validate_create(good_body(cpu=cpu, memory=memory))


@pytest.mark.parametrize(
    "body,fragment",
    [
        ({"upload_key": "uploads/../secrets.zip"}, "not one this service issued"),
        ({"upload_key": "someone-elses.zip"}, "not one this service issued"),
        ({"upload_key": "uploads/app.zip"}, "not one this service issued"),
        ({"upload_key": UPLOAD + "x"}, "not one this service issued"),
        ({"upload_key": 7}, "must be a string"),
        ({"label": ""}, "label is required"),
        ({"label": "x" * 201}, "at most 200"),
        ({"label": 7}, "must be a string"),
        ({"description": "x" * 2001}, "at most 2000"),
        ({"access_mode": "team"}, "reserved"),
        ({"access_mode": "organizations"}, "reserved"),
        ({"access_mode": "client_magic_link"}, "reserved"),
        ({"access_mode": "everyone"}, "all_users or users"),
        ({"allowed_emails": "jake@stratevi.com"}, "must be a list"),
        ({"allowed_emails": ["nope"]}, "not an email address"),
        ({"allowed_emails": [7]}, "list of strings"),
        ({"idle_minutes": 0}, "between 1 and 1440"),
        ({"idle_minutes": 1441}, "between 1 and 1440"),
        ({"idle_minutes": True}, "whole number"),
        ({"max_session_hours": -1}, "between 0 and 168"),
        ({"max_session_hours": 169}, "between 0 and 168"),
        ({"expires_at": -1}, "epoch seconds or null"),
        ({"expires_at": "tomorrow"}, "epoch seconds or null"),
        ({"packages": "shiny"}, "must be a list"),
        ({"packages": ["shiny; rm -rf /"]}, "not an R package name"),
        ({"packages": ["1shiny"]}, "not an R package name"),
        ({"packages": ["shiny-extra"]}, "not an R package name"),
        ({"packages": [7]}, "list of strings"),
        ({"packages": ["p"] * 400}, "at most 300"),
    ],
)
def test_the_create_bodies_the_contract_refuses(body, fragment):
    with pytest.raises(creation.CreateError, match=fragment):
        creation.validate_create(good_body(**body))


def test_an_unknown_field_is_refused_before_anything_is_provisioned():
    with pytest.raises(creation.CreateError, match="unknown field"):
        creation.validate_create(good_body(ecs_service="shiny-elsewhere"))


def test_users_mode_with_nobody_on_the_list_is_refused():
    """The row would be legal and the app unreachable by anyone -- fifteen
    minutes of CodeBuild for nothing."""
    with pytest.raises(creation.CreateError, match="at least one address"):
        creation.validate_create(good_body(access_mode="users", allowed_emails=[]))

    # all_users needs nobody.
    spec = creation.validate_create(
        good_body(access_mode="all_users", allowed_emails=[])
    )
    assert spec.allowed_emails == ()


def test_no_packages_at_all_is_allowed():
    """An app that needs only base R is unusual, not invalid."""
    assert creation.validate_create(good_body(packages=[])).packages == ()


@pytest.mark.parametrize("body", [[], "nope", 7, None])
def test_a_create_body_that_is_not_an_object_is_refused(body):
    with pytest.raises(creation.CreateError, match="JSON object"):
        creation.validate_create(body)


# --- derived values --------------------------------------------------------


@pytest.mark.parametrize(
    "cpu,workers", [(512, 1), (1024, 1), (2048, 1), (4096, 3), (8192, 7), (0, 1)]
)
def test_cpu_workers_leaves_a_core_for_shiny_and_never_returns_zero(cpu, workers):
    assert creation.cpu_workers(cpu) == workers


def test_the_package_list_is_space_separated_in_wizard_order():
    """The Dockerfile template's __PACKAGES__ token expects exactly this."""
    assert creation.package_list(("shiny", "ggplot2", "dplyr")) == (
        "shiny ggplot2 dplyr"
    )
    assert creation.package_list(()) == ""


# --- the environment contract ----------------------------------------------


CREATION_ENV = {
    "UPLOADS_BUCKET": "shiny-portal-uploads-652063276768",
    "CODEBUILD_PROJECT": "shiny-app-build",
    "APP_ROLE_BOUNDARY_ARN": "arn:aws:iam::652063276768:policy/shiny-app-boundary",
    "APP_DATA_BUCKET": "shiny-app-data-652063276768",
    "APP_DOMAIN": "tools.stratevi.com",
    "APP_SUBNET_IDS": "subnet-1,subnet-2",
    "APP_SECURITY_GROUP_ID": "sg-apps",
    "APP_EXECUTION_ROLE_ARN": "arn:aws:iam::652063276768:role/shiny-task-execution",
    "APP_LOG_GROUP": "/ecs/shiny/apps",
    "COGNITO_USER_POOL_ID": "us-east-1_hub",
    "COGNITO_CLIENT_ID": "sharedclientid",
}

BASE_ENV = {
    "ECS_CLUSTER": "shiny-cluster",
    "APPS_TABLE": "shiny-proxy-apps",
    "AUDIT_TABLE": "shiny-proxy-audit",
}


def test_with_no_creation_variables_creation_is_simply_off():
    """The backend has to be deployable before the P2a Terraform lands."""
    cfg = config_mod.from_env(BASE_ENV)
    assert cfg.creation is None
    assert cfg.creation_enabled() is False


def test_the_whole_creation_block_parses():
    cfg = config_mod.from_env({**BASE_ENV, **CREATION_ENV})
    assert cfg.creation_enabled() is True
    settings = cfg.creation
    assert settings.uploads_bucket == "shiny-portal-uploads-652063276768"
    assert settings.codebuild_project == "shiny-app-build"
    assert settings.role_boundary_arn.endswith("policy/shiny-app-boundary")
    assert settings.domain == "tools.stratevi.com"
    assert settings.subnet_ids == ("subnet-1", "subnet-2")


@pytest.mark.parametrize("missing", sorted(CREATION_ENV))
def test_a_partly_configured_pipeline_disables_creation_and_says_so(missing):
    """Half-configured is worse than off: by the time the third API call
    fails, a hostname is reserved and an ECR repository exists. So it is off
    -- loudly, naming the variable."""
    env = {**BASE_ENV, **CREATION_ENV}
    del env[missing]
    cfg = config_mod.from_env(env)

    assert cfg.creation is None
    assert missing in cfg.creation_error


def test_a_creation_misconfiguration_never_takes_the_proxy_down():
    """This task is in the request path for EVERY app. Refusing to boot over
    a broken wizard would take the dashboard, the model and the portal down
    to protect a feature nobody is currently using."""
    cfg = config_mod.from_env({**BASE_ENV, "UPLOADS_BUCKET": "only-this-one"})
    assert cfg.creation is None
    assert cfg.creation_error
    # Everything the proxy actually needs to serve traffic is still there.
    assert cfg.cluster == "shiny-cluster" and cfg.apps_table == "shiny-proxy-apps"


def test_a_real_contract_violation_is_still_a_startup_failure():
    """The degradation is scoped to the creation block and nothing else."""
    with pytest.raises(config_mod.ConfigError, match="APPS_TABLE"):
        config_mod.from_env({"ECS_CLUSTER": "c", "AUDIT_TABLE": "a"})


def test_the_boundary_arn_is_one_of_the_required_variables():
    """The outermost layer of the boundary invariant: no boundary, no
    creation -- so there is no code path to an unfenced app role."""
    assert "APP_ROLE_BOUNDARY_ARN" in config_mod.CREATION_VARIABLES
    cfg = config_mod.from_env(
        {**BASE_ENV, **CREATION_ENV, "APP_ROLE_BOUNDARY_ARN": "   "}
    )
    assert cfg.creation is None
    assert "APP_ROLE_BOUNDARY_ARN" in cfg.creation_error


def test_subnets_that_parse_to_nothing_disable_creation():
    cfg = config_mod.from_env(
        {**BASE_ENV, **CREATION_ENV, "APP_SUBNET_IDS": " , , "}
    )
    assert cfg.creation is None
    assert "APP_SUBNET_IDS" in cfg.creation_error
