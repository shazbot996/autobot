"""`autobot.gcp.secrets`: a pasted value goes to Secret Manager and is then cleared."""

import logging

import pytest

from autobot.config import GcpConfig
from autobot.gcp.client import GcpError
from autobot.gcp.secrets import clear, store_secret
from gcp.gcp_fakes import FakeGoogleCloud, ready_cloud

CONFIG = GcpConfig()
SECRET = "autobot-weather-api-key"
VALUE = b"AIza-the-pasted-key-0123456789"


def test_the_value_is_stored_as_a_new_version_and_the_buffer_is_cleared() -> None:
    cloud = ready_cloud(CONFIG)
    value = bytearray(VALUE)
    version = store_secret(cloud, CONFIG, SECRET, value)
    assert version.endswith(f"/secrets/{SECRET}/versions/2")
    assert cloud.stored == {SECRET: [VALUE]}
    assert cloud.secrets[SECRET] == ["ENABLED", "ENABLED"]
    assert value == bytearray(len(VALUE))
    # The call is recorded by the secret's name, never by its value.
    assert cloud.made("add_secret_version") == [("add_secret_version", SECRET)]


def test_a_refused_identity_leaves_the_buffer_cleared_and_nothing_called() -> None:
    cloud = ready_cloud(CONFIG)
    cloud.refuse = (
        "refused: credentials resolve to owner@example.com, not autobot-agent"
    )
    value = bytearray(VALUE)
    with pytest.raises(GcpError) as raised:
        store_secret(cloud, CONFIG, SECRET, value)
    assert value == bytearray(len(VALUE))
    assert cloud.calls == [] and cloud.stored == {}
    text = str(raised.value)
    assert text == (
        f"{SECRET} was not stored in schiele-autobot-dev: refused: credentials "
        "resolve to owner@example.com, not autobot-agent"
    )
    assert "\n" not in text


def test_the_error_never_contains_the_value() -> None:
    cloud = ready_cloud(CONFIG)
    # A service that echoed the request back would not leak it either.
    cloud.errors["add_secret_version"] = GcpError(
        f"secretmanager.googleapis.com answered 400: bad payload {VALUE.decode()}"
    )
    value = bytearray(VALUE)
    with pytest.raises(GcpError) as raised:
        store_secret(cloud, CONFIG, SECRET, value)
    assert VALUE.decode() not in str(raised.value)
    assert "[the value]" in str(raised.value)
    assert value == bytearray(len(VALUE))
    assert cloud.stored == {}


def test_a_secret_autobot_does_not_expect_is_not_stored() -> None:
    cloud = ready_cloud(CONFIG)
    value = bytearray(VALUE)
    with pytest.raises(GcpError) as raised:
        store_secret(cloud, CONFIG, "someone-elses-secret", value)
    assert str(raised.value) == (
        "someone-elses-secret is not a secret autobot expects: not stored"
    )
    assert value == bytearray(len(VALUE))
    assert cloud.calls == []


def test_an_empty_paste_stores_nothing() -> None:
    cloud = FakeGoogleCloud()
    with pytest.raises(GcpError) as raised:
        store_secret(cloud, CONFIG, SECRET, bytearray())
    assert str(raised.value) == f"nothing was pasted: {SECRET} is unchanged"
    assert cloud.calls == []


def test_nothing_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    store_secret(ready_cloud(CONFIG), CONFIG, SECRET, bytearray(VALUE))
    assert caplog.records == []


def test_clear_zeroes_in_place() -> None:
    value = bytearray(b"abc")
    same = value
    clear(value)
    assert same == bytearray(3) and same is value
