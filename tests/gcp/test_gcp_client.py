"""`autobot.gcp.client`: the identity check, the host allowlist, one-line failures."""

import json
import os
from pathlib import Path
from typing import Any

import google.auth
import google.auth.transport.requests
import pytest
from google.oauth2 import service_account

from autobot.config import Config, GcpConfig, parse_config
from autobot.gcp import client
from autobot.gcp.client import (
    PROJECT,
    UNLIMITED,
    ApiKey,
    GcpError,
    RestGoogleCloud,
    bucket_resource,
    connect,
    live_limit,
    secret_resource,
)
from autobot.gcp.keyfile import NOT_IGNORED
from gcp.gcp_fakes import FakeGoogleCloud, git_ignores, write_key

ACCOUNT = "autobot-agent@schiele-autobot-dev.iam.gserviceaccount.com"
SECRET = "autobot-weather-api-key"


def config(**gcp: Any) -> Config:
    return parse_config(
        {
            "audio": {"input_device": "pipewire", "output_device": "pipewire"},
            "wake_phrases": [
                {"phrase": "autobot", "kind": "text_match", "target": "ack"}
            ],
            "gcp": gcp,
        }
    )


class FakeCredentials(service_account.Credentials):
    """Service-account credentials with an address and no key behind them."""

    def __init__(self, email: str) -> None:
        self._service_account_email = email


@pytest.fixture(autouse=True)
def _no_real_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test here reaches Google's sign-in unless it patches it itself."""

    def unreachable(*args: object, **kwargs: object) -> object:
        raise AssertionError("google.auth.default was reached")

    monkeypatch.setattr(google.auth, "default", unreachable)
    monkeypatch.delenv(client.CREDENTIALS_ENV_VAR, raising=False)


# --- connect ------------------------------------------------------------------


def test_without_a_key_file_nothing_of_google_auth_is_called(tmp_path: Path) -> None:
    with pytest.raises(GcpError) as raised:
        connect(config(), tmp_path, git_ignores)
    assert str(raised.value) == (
        "refused: no key at secrets/service-account-autobot-agent.json yet"
    )
    # The environment was not pointed anywhere either.
    assert client.CREDENTIALS_ENV_VAR not in os.environ


def test_a_key_git_does_not_ignore_is_not_used(tmp_path: Path) -> None:
    write_key(GcpConfig(), tmp_path)
    with pytest.raises(GcpError) as raised:
        connect(config(), tmp_path, lambda repo, path: NOT_IGNORED)
    assert "git does not ignore" in str(raised.value)
    assert client.CREDENTIALS_ENV_VAR not in os.environ


def test_another_identity_is_refused_in_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_key(GcpConfig(), tmp_path)
    other = "someone-else@other-project.iam.gserviceaccount.com"
    monkeypatch.setattr(
        google.auth, "default", lambda scopes: (FakeCredentials(other), "p")
    )
    with pytest.raises(GcpError) as raised:
        connect(config(), tmp_path, git_ignores)
    assert (
        str(raised.value) == f"refused: credentials resolve to {other}, not {ACCOUNT}"
    )
    # The refused key is not left for anything started later in the process.
    assert client.CREDENTIALS_ENV_VAR not in os.environ


def test_the_owners_own_login_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_key(GcpConfig(), tmp_path)

    class UserCredentials:
        account = "owner@example.com"

    monkeypatch.setattr(google.auth, "default", lambda scopes: (UserCredentials(), "p"))
    with pytest.raises(GcpError) as raised:
        connect(config(), tmp_path, git_ignores)
    assert str(raised.value) == (
        f"refused: credentials resolve to owner@example.com, not {ACCOUNT}"
    )


def test_a_file_that_is_not_a_key_is_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_key(GcpConfig(), tmp_path)

    def broken(scopes: object) -> object:
        raise ValueError("the file's contents, which must not be shown")

    monkeypatch.setattr(google.auth, "default", broken)
    with pytest.raises(GcpError) as raised:
        connect(config(), tmp_path, git_ignores)
    assert str(raised.value) == (
        "cannot sign in with secrets/service-account-autobot-agent.json: ValueError"
    )


def test_the_service_account_connects_through_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = write_key(GcpConfig(), tmp_path)
    asked: list[object] = []

    def default(scopes: list[str]) -> tuple[object, str]:
        # Application Default Credentials are pointed at the key by now.
        asked.append((os.environ[client.CREDENTIALS_ENV_VAR], scopes))
        return FakeCredentials(ACCOUNT), "schiele-autobot-dev"

    monkeypatch.setattr(google.auth, "default", default)
    # The session that signs each call is Google's; it is not built here.
    sessions: list[object] = []
    monkeypatch.setattr(
        google.auth.transport.requests, "AuthorizedSession", sessions.append
    )
    cloud = connect(config(), tmp_path, git_ignores)
    assert isinstance(cloud, RestGoogleCloud)
    assert [type(credentials) for credentials in sessions] == [FakeCredentials]
    assert asked == [(str(key), ["https://www.googleapis.com/auth/cloud-platform"])]


# --- the REST client, against a fake session ----------------------------------


class Response:
    def __init__(self, status: int, data: object) -> None:
        self.status_code = status
        self.data = data

    def json(self) -> object:
        if self.data is None:
            raise ValueError("no JSON")
        return self.data


class Session:
    """Answers each request from a list, and keeps what was asked."""

    def __init__(self, *answers: Response | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self.requests.append({"method": method, "url": url, **kwargs})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def rest(*answers: Response | Exception, **gcp: Any) -> tuple[RestGoogleCloud, Session]:
    session = Session(*answers)
    return RestGoogleCloud(config(**gcp).gcp, session), session


def test_a_host_outside_the_allowlist_is_refused_before_any_request() -> None:
    cloud, session = rest()
    for url in (
        "https://speech.googleapis.com/v1/speech:recognize",
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        "https://example.com/",
        "http://serviceusage.googleapis.com/v1/projects/p/services",
        "https://storage.googleapis.com/storage/v1/b/x",
    ):
        with pytest.raises(GcpError) as raised:
            cloud._call("GET", url)
        assert str(raised.value).startswith("refused: ")
        assert "is not a Google Cloud API autobot uses" in str(raised.value)
    assert session.requests == []


def test_cloud_storage_is_allowed_only_once_a_bucket_is_listed() -> None:
    cloud, session = rest(Response(200, {"permissions": []}), buckets=["state"])
    assert cloud.bucket_exists("state")
    assert session.requests[0]["url"] == (
        "https://storage.googleapis.com/storage/v1/b/state/iam/testPermissions"
    )


def test_enabled_services_follows_the_pages() -> None:
    cloud, session = rest(
        Response(
            200,
            {
                "services": [{"config": {"name": "iam.googleapis.com"}}],
                "nextPageToken": "next",
            },
        ),
        Response(
            200, {"services": [{"name": "projects/1/services/weather.googleapis.com"}]}
        ),
    )
    assert cloud.enabled_services() == {"iam.googleapis.com", "weather.googleapis.com"}
    first, second = session.requests
    assert first["url"] == (
        "https://serviceusage.googleapis.com/v1/projects/schiele-autobot-dev/services"
    )
    assert first["params"] == {"filter": "state:ENABLED", "pageSize": "200"}
    assert second["params"]["pageToken"] == "next"


def test_permissions_are_tested_on_the_project_a_secret_and_a_bucket() -> None:
    cloud, session = rest(
        Response(200, {"permissions": ["serviceusage.services.list"]}),
        Response(200, {}),
        Response(200, {"permissions": ["storage.objects.get"]}),
        buckets=["state"],
    )
    wanted = ["serviceusage.services.list", "apikeys.keys.list"]
    assert cloud.test_permissions(PROJECT, wanted) == {"serviceusage.services.list"}
    assert cloud.test_permissions(secret_resource(SECRET), wanted) == frozenset()
    assert cloud.test_permissions(
        bucket_resource("state"), ["storage.objects.get"]
    ) == {"storage.objects.get"}
    urls = [request["url"] for request in session.requests]
    resource_manager = "https://cloudresourcemanager.googleapis.com"
    secret_manager = "https://secretmanager.googleapis.com"
    assert urls == [
        f"{resource_manager}/v1/projects/schiele-autobot-dev:testIamPermissions",
        f"{secret_manager}/v1/projects/schiele-autobot-dev/secrets/{SECRET}"
        + ":testIamPermissions",
        "https://storage.googleapis.com/storage/v1/b/state/iam/testPermissions",
    ]
    assert json.loads(session.requests[0]["data"]) == {"permissions": wanted}


def test_a_permission_that_cannot_be_tested_does_not_hide_the_others() -> None:
    bad = Response(400, {"error": {"message": "Permission x is not valid"}})
    cloud, session = rest(
        bad,
        Response(200, {"permissions": ["serviceusage.services.enable"]}),
        bad,
    )
    held = cloud.test_permissions(
        PROJECT, ["serviceusage.services.enable", "billing.resourceAssociations.create"]
    )
    assert held == {"serviceusage.services.enable"}
    assert len(session.requests) == 3


def test_secret_versions_are_states_never_values() -> None:
    cloud, session = rest(
        Response(200, {"versions": [{"state": "ENABLED"}, {"state": "DESTROYED"}]})
    )
    assert cloud.secret_versions(SECRET) == ("ENABLED", "DESTROYED")
    assert ":access" not in session.requests[0]["url"]


def test_a_secret_version_is_added_and_the_body_is_cleared() -> None:
    cloud, session = rest(
        Response(200, {"name": f"projects/1/secrets/{SECRET}/versions/2"})
    )
    value = bytearray(b"the-pasted-key")
    assert cloud.add_secret_version(SECRET, value).endswith("/versions/2")
    request = session.requests[0]
    assert request["method"] == "POST"
    assert request["url"].endswith(f"/secrets/{SECRET}:addVersion")
    assert json.loads(request["data"]) == {"payload": {"data": "dGhlLXBhc3RlZC1rZXk="}}


def test_a_secret_value_is_the_latest_version_decoded() -> None:
    cloud, session = rest(
        Response(
            200,
            {
                "name": f"projects/1/secrets/{SECRET}/versions/2",
                "payload": {"data": "dGhlLXBhc3RlZC1rZXk="},
            },
        )
    )
    value = cloud.secret_value(SECRET)
    assert isinstance(value, bytearray)
    assert value == b"the-pasted-key"
    request = session.requests[0]
    assert request["method"] == "GET"
    assert request["url"] == (
        "https://secretmanager.googleapis.com/v1/projects/schiele-autobot-dev/"
        f"secrets/{SECRET}/versions/latest:access"
    )


def test_a_secret_with_no_version_is_one_line_naming_it() -> None:
    cloud, _ = rest(Response(404, {"error": {"message": "Secret Version\nnot found"}}))
    with pytest.raises(GcpError) as raised:
        cloud.secret_value(SECRET)
    assert str(raised.value) == f"secret {SECRET} has no enabled version (HTTP 404)"


def test_a_secret_with_no_readable_value_is_one_line_without_the_payload() -> None:
    bodies: tuple[dict[str, Any], ...] = (
        {},
        {"payload": {}},
        {"payload": {"data": ""}},
        {"payload": {"data": "not base64, and never shown"}},
    )
    for body in bodies:
        cloud, _ = rest(Response(200, body))
        with pytest.raises(GcpError) as raised:
            cloud.secret_value(SECRET)
        line = str(raised.value)
        assert line.startswith(f"secret {SECRET} has ")
        assert "\n" not in line
        assert "never shown" not in line


def test_a_refused_read_of_a_secret_is_one_line_naming_the_host() -> None:
    cloud, _ = rest(Response(403, {"error": {"message": "Permission denied"}}))
    with pytest.raises(GcpError) as raised:
        cloud.secret_value(SECRET)
    assert str(raised.value) == (
        "secretmanager.googleapis.com answered 403: Permission denied"
    )


def test_the_fake_cloud_answers_a_secrets_last_value_and_keeps_what_it_gave() -> None:
    cloud = FakeGoogleCloud(secrets={SECRET: []})
    with pytest.raises(GcpError) as raised:
        cloud.secret_value(SECRET)
    assert str(raised.value) == f"secret {SECRET} has no enabled version (HTTP 404)"
    cloud.add_secret_version(SECRET, bytearray(b"first"))
    cloud.add_secret_version(SECRET, bytearray(b"second"))
    value = cloud.secret_value(SECRET)
    assert value == bytearray(b"second")
    # The caller clears what it was given, and the test can see that it did.
    value[:] = bytes(len(value))
    assert cloud.given == [bytearray(6)]
    # A value a test sets comes before the stored versions.
    cloud.values[SECRET] = b"preset"
    assert cloud.secret_value(SECRET) == b"preset"
    assert cloud.made("secret_value") == [("secret_value", SECRET)] * 3


def test_api_keys_carry_their_restriction_and_no_value() -> None:
    cloud, _ = rest(
        Response(
            200,
            {
                "keys": [
                    {
                        "name": "projects/1/locations/global/keys/a",
                        "displayName": "autobot-weather",
                        "restrictions": {
                            "apiTargets": [{"service": "weather.googleapis.com"}]
                        },
                    },
                    {
                        "name": "projects/1/locations/global/keys/b",
                        "displayName": "open",
                    },
                ]
            },
        )
    )
    assert cloud.api_keys() == (
        ApiKey(
            "projects/1/locations/global/keys/a",
            "autobot-weather",
            ("weather.googleapis.com",),
        ),
        ApiKey("projects/1/locations/global/keys/b", "open", ()),
    )


def test_a_quota_is_read_by_its_id() -> None:
    info = {"dimensionsInfos": [{"details": {"value": "100"}, "dimensions": {}}]}
    cloud, session = rest(
        Response(200, info), Response(404, {"error": {"message": "x"}})
    )
    assert cloud.quota_limit("weather.googleapis.com", "PerDay", "us-east5") == 100
    assert session.requests[0]["url"] == (
        "https://cloudquotas.googleapis.com/v1/projects/schiele-autobot-dev/locations/"
        "global/services/weather.googleapis.com/quotaInfos/PerDay"
    )
    # An id the API does not have is no quota, not a failure.
    assert cloud.quota_limit("weather.googleapis.com", "Nope", "us-east5") is None


def test_the_live_limit_is_the_highest_that_applies_to_the_region() -> None:
    def info(value: str, **dimensions: str) -> dict[str, Any]:
        return {"details": {"value": value}, "dimensions": dimensions}

    regional = {
        "dimensionsInfos": [
            info("10", region="us-east5", base_model="a"),
            info("60", region="us-east5", base_model="b"),
            info("9000", region="us-central1", base_model="a"),
        ]
    }
    assert live_limit(regional, "us-east5") == 60
    assert live_limit({"dimensionsInfos": [info("100")]}, "us-east5") == 100
    assert live_limit({"dimensionsInfos": [info("-1")]}, "us-east5") == UNLIMITED
    assert (
        live_limit({"dimensionsInfos": [{"dimensions": {}}]}, "us-east5") == UNLIMITED
    )
    assert live_limit({}, "us-east5") == UNLIMITED
    assert live_limit(regional, "europe-west1") == UNLIMITED
    # With a model, the first value that applies to it is the one in force:
    # the list runs from the most specific to the least.
    listed = {
        "dimensionsInfos": [
            info("10", region="us-east5", base_model="flash"),
            info("9000", region="us-central1", base_model="flash"),
            info("600", region="us-east5"),
            info("5000"),
        ]
    }
    assert live_limit(listed, "us-east5", "flash") == 10
    assert live_limit(listed, "us-east5", "pro") == 600
    assert live_limit(listed, "europe-west1", "pro") == 5000
    assert live_limit(listed, "us-east5") == 5000


def test_a_missing_bucket_is_not_a_failure() -> None:
    cloud, _ = rest(Response(404, {"error": {"message": "x"}}), buckets=["state"])
    assert not cloud.bucket_exists("state")


def test_an_http_error_is_one_line_naming_the_host() -> None:
    cloud, _ = rest(
        Response(
            403,
            {"error": {"message": "Permission denied\non resource\n  project x."}},
        )
    )
    with pytest.raises(GcpError) as raised:
        cloud.enabled_services()
    assert str(raised.value) == (
        "serviceusage.googleapis.com answered 403: Permission denied on resource project x."
    )


def test_an_error_with_no_body_is_still_one_line() -> None:
    cloud, _ = rest(Response(502, None))
    with pytest.raises(GcpError) as raised:
        cloud.api_keys()
    assert str(raised.value) == "apikeys.googleapis.com answered 502: no reason given"


def test_no_network_is_one_line_not_a_traceback() -> None:
    cloud, _ = rest(ConnectionError("Name or service\nnot known"))
    with pytest.raises(GcpError) as raised:
        cloud.enabled_services()
    assert str(raised.value) == (
        "cannot reach serviceusage.googleapis.com: "
        "ConnectionError: Name or service not known"
    )


def test_the_gemini_models_are_listed_from_vertex_ai_newest_first() -> None:
    def named(model: str) -> dict[str, str]:
        return {"name": f"publishers/google/models/{model}", "launchStage": "GA"}

    cloud, session = rest(
        Response(
            200,
            {
                "publisherModels": [
                    named("gemini-2.5-flash"),
                    named("text-embedding-005"),
                ],
                "nextPageToken": "more",
            },
        ),
        Response(
            200,
            {"publisherModels": [named("gemini-3.5-flash-lite"), named("imagen-4")]},
        ),
    )
    assert cloud.gemini_models() == ("gemini-3.5-flash-lite", "gemini-2.5-flash")
    assert session.requests[0]["url"] == (
        "https://aiplatform.googleapis.com/v1beta1/publishers/google/models"
    )
    assert session.requests[1]["params"]["pageToken"] == "more"


def test_the_developer_api_does_not_list_its_models_to_the_account() -> None:
    cloud, session = rest(gemini_api="generativelanguage.googleapis.com")
    with pytest.raises(GcpError):
        cloud.gemini_models()
    assert session.requests == []
