"""autobot's connection to Google Cloud, as its service account and nobody else.

`connect` is the only way in. It refuses unless the key file is safe to use
(`keyfile.check_key`), points Application Default Credentials at that key,
and then checks which identity the credentials resolve to: anything but the
configured service account is refused before a single call is made (GC-4). It
never asks for default credentials without the key, so it cannot fall through
to the owner's own login.

`GoogleCloud` is the one seam every check uses, so the checks are tested with
a fake and never with a key or a network. `RestGoogleCloud` implements it over
the REST endpoints of the APIs autobot needs (`needs.APIS`) and refuses any
other host (GC-13). Every failure is a `GcpError` whose message is one line.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

from autobot.config import GEMINI_VERTEX_AI, Config, GcpConfig
from autobot.gcp.keyfile import GitCheckIgnore, check_key, git_check_ignore
from autobot.gcp.needs import allowed_hosts

CREDENTIALS_ENV_VAR = "GOOGLE_APPLICATION_CREDENTIALS"
SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)
# How long one call may take, in seconds.
TIMEOUT = 20.0

# The resource a permission is tested on: the project, or one secret or bucket.
PROJECT = "project"
# A quota with no limit, as Google Cloud writes it.
UNLIMITED = -1


def secret_resource(name: str) -> str:
    return f"secrets/{name}"


def bucket_resource(name: str) -> str:
    return f"buckets/{name}"


class GcpError(Exception):
    """A Google Cloud call failed or was refused. The message is one line."""

    def __init__(self, message: str) -> None:
        super().__init__(" ".join(str(message).split()))


@dataclass(frozen=True)
class ApiKey:
    """An API key of the project: never its value."""

    # The resource name, `projects/N/locations/global/keys/ID`.
    name: str
    display_name: str
    # The APIs the key is restricted to; empty when it is unrestricted.
    api_targets: tuple[str, ...]


class GoogleCloud(Protocol):
    """What the checks ask of Google Cloud, as the service account."""

    def enabled_services(self) -> frozenset[str]:
        """The APIs enabled in the project."""
        ...

    def test_permissions(
        self, resource: str, permissions: Sequence[str]
    ) -> frozenset[str]:
        """Which of `permissions` the account holds on the project, a secret or a bucket."""
        ...

    def secret_versions(self, secret: str) -> tuple[str, ...]:
        """The state of each version of a secret, as `ENABLED`. Never a value."""
        ...

    def add_secret_version(self, secret: str, payload: bytearray) -> str:
        """Store `payload` as a new version of a secret; the version's name."""
        ...

    def secret_value(self, secret: str) -> bytearray:
        """The value of a secret's latest version, which the caller clears."""
        ...

    def api_keys(self) -> tuple[ApiKey, ...]:
        """The project's API keys, without their values."""
        ...

    def quota_limit(
        self, api: str, metric: str, region: str, base_model: str = ""
    ) -> int | None:
        """The live limit of a quota id; None when the API has no such quota.

        `base_model` names the model, for a quota that is counted per model.
        """
        ...

    def bucket_exists(self, bucket: str) -> bool: ...

    def gemini_models(self) -> tuple[str, ...]:
        """The ids of the Gemini models the chosen API offers now, newest first."""
        ...


def _identity(credentials: object) -> str:
    """Who some credentials are, for the refusal's one line."""
    for attribute in ("service_account_email", "signer_email", "account"):
        value = getattr(credentials, attribute, None)
        if isinstance(value, str) and value:
            return value
    return f"{type(credentials).__name__} credentials"


def connect(
    config: Config, repo: Path, git: GitCheckIgnore = git_check_ignore
) -> GoogleCloud:
    """Connect as the configured service account, or raise `GcpError`.

    Nothing of `google.auth` is called until the key file is *ready*.
    """
    gcp = config.gcp
    key = check_key(gcp, repo, git)
    if not key.ready:
        raise GcpError(f"refused: {key.reason}")
    before = os.environ.get(CREDENTIALS_ENV_VAR)
    os.environ[CREDENTIALS_ENV_VAR] = str(key.path)

    def refuse(message: str) -> GcpError:
        # A key that is refused is not left where an agent started later in
        # this process would find it.
        if before is None:
            os.environ.pop(CREDENTIALS_ENV_VAR, None)
        else:
            os.environ[CREDENTIALS_ENV_VAR] = before
        return GcpError(message)

    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        credentials, _ = google.auth.default(scopes=list(SCOPES))
    except Exception as error:  # noqa: BLE001
        # A key file that is not a key, a library that is not installed: one
        # line, and never the file's contents.
        raise refuse(
            f"cannot sign in with {key.shown}: {type(error).__name__}"
        ) from None
    email = getattr(credentials, "service_account_email", None)
    if (
        not isinstance(credentials, service_account.Credentials)
        or email != gcp.service_account
    ):
        raise refuse(
            f"refused: credentials resolve to {_identity(credentials)}, "
            f"not {gcp.service_account}"
        )
    session = AuthorizedSession(credentials)  # type: ignore[no-untyped-call]
    return RestGoogleCloud(gcp, session)


class RestGoogleCloud:
    """`GoogleCloud` over the REST APIs, through a session that signs each call.

    `session` needs one method, `request(method, url, **kwargs)`, answering
    with an object that has `status_code` and `json()`: an
    `AuthorizedSession` in a real run, a fake in the tests.
    """

    def __init__(self, config: GcpConfig, session: Any) -> None:
        self.config = config
        self.session = session
        self.hosts = allowed_hosts(config)

    # --- the one way out ------------------------------------------------------

    def _call(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | Sequence[str]] | None = None,
        body: bytes | None = None,
        missing_ok: bool = False,
    ) -> dict[str, Any] | None:
        """One call. None for a 404 when `missing_ok`; `GcpError` for any failure."""
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme != "https" or host not in self.hosts:
            raise GcpError(
                f"refused: {host or url} is not a Google Cloud API autobot uses"
            )
        headers = {"Content-Type": "application/json"} if body is not None else None
        try:
            response = self.session.request(
                method, url, params=params, data=body, headers=headers, timeout=TIMEOUT
            )
            status = int(response.status_code)
        except Exception as error:  # noqa: BLE001
            # No network, a refused token, a timeout: the cause in one line.
            raise GcpError(f"cannot reach {host}: {_cause(error)}") from None
        if status == 404 and missing_ok:
            return None
        try:
            data = response.json()
        except ValueError:
            data = {}
        if status >= 400:
            raise GcpError(f"{host} answered {status}: {_error_message(data)}")
        return data if isinstance(data, dict) else {}

    def _pages(
        self, url: str, key: str, params: Mapping[str, str] | None = None
    ) -> list[dict[str, Any]]:
        """Every item of a paged list."""
        items: list[dict[str, Any]] = []
        query = dict(params or {})
        for _ in range(50):
            data = self._call("GET", url, params=dict(query)) or {}
            items += [item for item in data.get(key, []) if isinstance(item, dict)]
            token = data.get("nextPageToken")
            if not token:
                break
            query["pageToken"] = str(token)
        return items

    def _project(self) -> str:
        return quote(self.config.project, safe="")

    # --- the protocol ---------------------------------------------------------

    def enabled_services(self) -> frozenset[str]:
        services = self._pages(
            f"https://serviceusage.googleapis.com/v1/projects/{self._project()}/services",
            "services",
            {"filter": "state:ENABLED", "pageSize": "200"},
        )
        names = set()
        for service in services:
            config = service.get("config")
            name = config.get("name") if isinstance(config, dict) else None
            names.add(str(name or str(service.get("name", "")).rsplit("/", 1)[-1]))
        return frozenset(names)

    def test_permissions(
        self, resource: str, permissions: Sequence[str]
    ) -> frozenset[str]:
        wanted = list(dict.fromkeys(permissions))
        if not wanted:
            return frozenset()
        try:
            return self._test(resource, wanted)
        except GcpError as error:
            # One permission that cannot be tested on this kind of resource
            # fails the whole call: ask for each alone, and count such a
            # permission as not held.
            if len(wanted) == 1 or " answered 400:" not in str(error):
                raise
        held: set[str] = set()
        for permission in wanted:
            try:
                held |= self._test(resource, [permission])
            except GcpError as error:
                if " answered 400:" not in str(error):
                    raise
        return frozenset(held)

    def _test(self, resource: str, permissions: Sequence[str]) -> frozenset[str]:
        kind, _, name = resource.partition("/")
        name = quote(name, safe="")
        if resource == PROJECT:
            data = self._post(
                "https://cloudresourcemanager.googleapis.com/v1/projects/"
                f"{self._project()}:testIamPermissions",
                {"permissions": list(permissions)},
            )
        elif kind == "secrets":
            data = self._post(
                "https://secretmanager.googleapis.com/v1/projects/"
                f"{self._project()}/secrets/{name}:testIamPermissions",
                {"permissions": list(permissions)},
            )
        elif kind == "buckets":
            data = (
                self._call(
                    "GET",
                    f"https://storage.googleapis.com/storage/v1/b/{name}/iam/testPermissions",
                    params={"permissions": list(permissions)},
                )
                or {}
            )
        else:
            raise GcpError(f"cannot test permissions on {resource}")
        held = data.get("permissions", [])
        return (
            frozenset(str(item) for item in held)
            if isinstance(held, list)
            else frozenset()
        )

    def _post(self, url: str, payload: Mapping[str, object]) -> dict[str, Any]:
        return self._call("POST", url, body=json.dumps(payload).encode()) or {}

    def secret_versions(self, secret: str) -> tuple[str, ...]:
        versions = self._pages(
            "https://secretmanager.googleapis.com/v1/projects/"
            f"{self._project()}/secrets/{quote(secret, safe='')}/versions",
            "versions",
            {"pageSize": "100"},
        )
        return tuple(str(version.get("state", "")) for version in versions)

    def add_secret_version(self, secret: str, payload: bytearray) -> str:
        # The request body is built here and cleared after the call. The
        # HTTP library copies it on the way out; those copies are its own.
        body = bytearray(b'{"payload":{"data":"')
        body += base64.b64encode(payload)
        body += b'"}}'
        try:
            data = self._call(
                "POST",
                "https://secretmanager.googleapis.com/v1/projects/"
                f"{self._project()}/secrets/{quote(secret, safe='')}:addVersion",
                body=bytes(body),
            )
        finally:
            body[:] = bytes(len(body))
        return str((data or {}).get("name", ""))

    def secret_value(self, secret: str) -> bytearray:
        # The response is parsed by the HTTP library, whose copies are its
        # own. No failure here carries the value: a payload that cannot be
        # read is named by the secret alone.
        data = self._call(
            "GET",
            "https://secretmanager.googleapis.com/v1/projects/"
            f"{self._project()}/secrets/{quote(secret, safe='')}"
            "/versions/latest:access",
            missing_ok=True,
        )
        if data is None:
            raise GcpError(f"secret {secret} has no enabled version (HTTP 404)")
        payload = data.get("payload")
        encoded = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(encoded, str) or not encoded:
            raise GcpError(f"secret {secret} has no value in its latest version")
        try:
            value = bytearray(base64.b64decode(encoded, validate=True))
        except ValueError:
            raise GcpError(
                f"secret {secret} has a latest version that cannot be read"
            ) from None
        if not value:
            raise GcpError(f"secret {secret} has no value in its latest version")
        return value

    def api_keys(self) -> tuple[ApiKey, ...]:
        keys = self._pages(
            "https://apikeys.googleapis.com/v2/projects/"
            f"{self._project()}/locations/global/keys",
            "keys",
            {"pageSize": "300"},
        )
        found = []
        for key in keys:
            restrictions = key.get("restrictions")
            targets = (
                restrictions.get("apiTargets", [])
                if isinstance(restrictions, dict)
                else []
            )
            found.append(
                ApiKey(
                    name=str(key.get("name", "")),
                    display_name=str(key.get("displayName", "")),
                    api_targets=tuple(
                        str(target.get("service", ""))
                        for target in targets
                        if isinstance(target, dict)
                    ),
                )
            )
        return tuple(found)

    def quota_limit(
        self, api: str, metric: str, region: str, base_model: str = ""
    ) -> int | None:
        data = self._call(
            "GET",
            "https://cloudquotas.googleapis.com/v1/projects/"
            f"{self._project()}/locations/global/services/{quote(api, safe='')}"
            f"/quotaInfos/{quote(metric, safe='')}",
            missing_ok=True,
        )
        if data is None:
            return None
        return live_limit(data, region, base_model)

    def bucket_exists(self, bucket: str) -> bool:
        data = self._call(
            "GET",
            "https://storage.googleapis.com/storage/v1/b/"
            f"{quote(bucket, safe='')}/iam/testPermissions",
            params={"permissions": ["storage.objects.list"]},
            missing_ok=True,
        )
        return data is not None

    def gemini_models(self) -> tuple[str, ...]:
        if self.config.gemini_api != GEMINI_VERTEX_AI:
            raise GcpError(
                f"{self.config.gemini_api} lists its models only to an API key"
            )
        models = self._pages(
            f"https://{GEMINI_VERTEX_AI}/v1beta1/publishers/google/models",
            "publisherModels",
            {"pageSize": "200"},
        )
        return model_ids(models)


def model_ids(publisher_models: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """The Gemini model ids in a list of Vertex AI publisher models, newest first.

    A model's name is `publishers/google/models/<id>`. Only Gemini models are
    kept: the publisher also lists embedding, image and other models, which
    the question agent cannot call.
    """
    ids = {str(model.get("name", "")).rsplit("/", 1)[-1] for model in publisher_models}
    return tuple(
        sorted((name for name in ids if name.startswith("gemini-")), reverse=True)
    )


def live_limit(quota_info: Mapping[str, Any], region: str, base_model: str = "") -> int:
    """The limit in force for `region`, from a Cloud Quotas `QuotaInfo`.

    A quota with dimensions has one value per combination of them, listed
    from the most specific to the least. The values that apply to the region
    are the ones that name it and the ones that name no region.

    With a `base_model`, the limit is the first value that applies to that
    model in that region: the most specific one, which is the one in force.
    Without one, the highest value that applies is what a caller could reach,
    so that is the limit compared with the intended cap. A value that is
    missing or negative is no limit at all.
    """
    wanted = {"region": region}
    if base_model:
        wanted["base_model"] = base_model
    infos = quota_info.get("dimensionsInfos")
    values: list[int] = []
    for info in infos if isinstance(infos, list) else []:
        if not isinstance(info, dict):
            continue
        dimensions = info.get("dimensions")
        named = dimensions if isinstance(dimensions, dict) else {}
        if base_model:
            if any(wanted.get(key) != value for key, value in named.items()):
                continue
        elif named.get("region") not in (None, region):
            continue
        details = info.get("details")
        raw = details.get("value") if isinstance(details, dict) else None
        try:
            value = int(str(raw))
        except ValueError:
            return UNLIMITED
        if value < 0:
            return UNLIMITED
        if base_model:
            return value
        values.append(value)
    return max(values) if values else UNLIMITED


def _cause(error: Exception) -> str:
    """An exception as a few words: its kind, and its first line when short."""
    text = " ".join(str(error).split())
    name = type(error).__name__
    return f"{name}: {text[:120]}" if text else name


def _error_message(data: object) -> str:
    """The message of a Google API error body, as one line."""
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return " ".join(str(error["message"]).split())[:200]
        if isinstance(error, str):
            return error[:200]
    return "no reason given"
