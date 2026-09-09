"""The environment contract. These names must match the proxy/ Terraform stack."""

from __future__ import annotations

import json
import logging

import pytest

from proxy_app import config

COMPLETE = {
    "ECS_CLUSTER": "shiny-cluster",
    "APPS_TABLE": "shiny-proxy-apps",
    "AUDIT_TABLE": "shiny-proxy-audit",
}


def test_the_three_required_variables_are_enough():
    cfg = config.from_env(COMPLETE)
    assert cfg.cluster == "shiny-cluster"
    assert cfg.apps_table == "shiny-proxy-apps"
    assert cfg.audit_table == "shiny-proxy-audit"
    assert cfg.port == 8080
    assert cfg.log_level == "info"
    assert cfg.region == ""


def test_optional_variables_override_the_defaults():
    cfg = config.from_env(
        {**COMPLETE, "AWS_REGION": "us-east-1", "PORT": "9090", "LOG_LEVEL": "DEBUG"}
    )
    assert cfg.region == "us-east-1"
    assert cfg.port == 9090
    assert cfg.log_level == "debug"


@pytest.mark.parametrize("missing", ["ECS_CLUSTER", "APPS_TABLE", "AUDIT_TABLE"])
def test_a_missing_required_variable_refuses_to_start(missing):
    env = {k: v for k, v in COMPLETE.items() if k != missing}
    with pytest.raises(config.ConfigError, match=missing):
        config.from_env(env)


def test_every_missing_variable_is_named_at_once():
    with pytest.raises(config.ConfigError) as raised:
        config.from_env({})
    message = str(raised.value)
    assert all(name in message for name in COMPLETE)


@pytest.mark.parametrize("port", ["", "  "])
def test_a_blank_port_falls_back_to_the_default(port):
    assert config.from_env({**COMPLETE, "PORT": port}).port == 8080


@pytest.mark.parametrize("port", ["nope", "0", "-1", "65536", "8080.5"])
def test_a_bad_port_refuses_to_start(port):
    with pytest.raises(config.ConfigError, match="PORT"):
        config.from_env({**COMPLETE, "PORT": port})


def test_whitespace_around_values_is_ignored():
    cfg = config.from_env({k: f"  {v}  " for k, v in COMPLETE.items()})
    assert cfg.cluster == "shiny-cluster"


# --- logging ---------------------------------------------------------------


def _format(record: logging.LogRecord) -> dict:
    return json.loads(config.JsonFormatter().format(record))


def test_log_lines_are_json_with_the_extras_flattened_in():
    record = logging.LogRecord(
        "proxy", logging.INFO, __file__, 1, "refused", None, None
    )
    record.host = "model.tools.stratevi.com"
    record.outcome = "not_entitled"

    payload = _format(record)
    assert payload["level"] == "info"
    assert payload["msg"] == "refused"
    assert payload["host"] == "model.tools.stratevi.com"
    assert payload["outcome"] == "not_entitled"
    assert payload["time"].endswith("+00:00")


def test_an_unserializable_extra_does_not_take_the_log_line_down():
    record = logging.LogRecord("proxy", logging.INFO, __file__, 1, "x", None, None)
    record.thing = object()
    assert "object object at" in _format(record)["thing"]


def test_unknown_log_levels_fall_back_to_info():
    logger = config.configure_logging("shouting")
    assert logging.getLogger().level == logging.INFO
    assert logger.name == "proxy"


def test_debug_does_not_turn_on_botocore_s_several_hundred_lines_per_call():
    config.configure_logging("debug")
    try:
        assert logging.getLogger().level == logging.DEBUG
        for noisy in ("botocore", "aiohttp.access", "urllib3"):
            assert logging.getLogger(noisy).level == logging.WARNING
    finally:
        config.configure_logging("info")
