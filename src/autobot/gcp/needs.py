"""What autobot needs in Google Cloud, as data: APIs, roles, permissions, secrets.

The Google Cloud screen checks the project against these catalogues, and the
commands it shows are built from them. An agent that starts calling a new
Google API, or needs a new role or secret, adds it here (GC-5, GC-6, GC-9).
"""

from __future__ import annotations

from dataclasses import dataclass

from autobot.config import GEMINI_DEVELOPER_API, GEMINI_VERTEX_AI, GcpConfig

# Where a role is granted: on the project, or on one resource (GC-6).
ON_PROJECT = "project"
ON_SECRET = "secret"
ON_BUCKET = "bucket"


@dataclass(frozen=True)
class Api:
    name: str
    needed_for: str


WEATHER_API = "weather.googleapis.com"
SECRET_MANAGER_API = "secretmanager.googleapis.com"
STORAGE_API = "storage.googleapis.com"

# GC-5's table, in its order.
APIS = (
    Api(
        "serviceusage.googleapis.com",
        "checking which APIs are enabled, and reading quotas",
    ),
    Api("cloudresourcemanager.googleapis.com", "the permission check (GC-8)"),
    Api("iam.googleapis.com", "creating the service account and its key"),
    Api(SECRET_MANAGER_API, "the secrets (GC-9)"),
    Api(GEMINI_VERTEX_AI, "Gemini on Vertex AI, when that is the chosen API (GC-11)"),
    Api(WEATHER_API, "the weather agent's lookup (AG-6)"),
    Api("apikeys.googleapis.com", "creating and restricting the weather API key"),
    Api("cloudquotas.googleapis.com", "setting the caps (GC-10)"),
)
# Needed only when the configuration asks for them.
DEVELOPER_API = Api(
    GEMINI_DEVELOPER_API, "Gemini through the Developer API, when that is chosen"
)
STORAGE = Api(STORAGE_API, "the Cloud Storage step, once a bucket is listed")

# The one host outside the APIs that autobot connects to: Google's sign-in
# exchanges the service account's key for a token there.
TOKEN_HOST = "oauth2.googleapis.com"


def needed_apis(config: GcpConfig) -> tuple[Api, ...]:
    """The APIs this configuration needs enabled, in the order they are listed."""
    apis = [
        api
        for api in APIS
        if api.name != GEMINI_VERTEX_AI or config.gemini_api == GEMINI_VERTEX_AI
    ]
    if config.gemini_api == GEMINI_DEVELOPER_API:
        apis.append(DEVELOPER_API)
    if config.buckets:
        apis.append(STORAGE)
    return tuple(apis)


def allowed_hosts(config: GcpConfig) -> frozenset[str]:
    """The hosts a check may connect to: the needed APIs and nothing else (GC-13)."""
    return frozenset(api.name for api in needed_apis(config))


@dataclass(frozen=True)
class Role:
    name: str
    # `ON_PROJECT`, `ON_SECRET` or `ON_BUCKET`.
    granted_on: str
    lets_autobot: str
    # The permissions of the role that autobot uses, which the permission
    # check asks Google Cloud about (GC-8).
    permissions: tuple[str, ...]


ROLES = (
    Role(
        "roles/serviceusage.serviceUsageViewer",
        ON_PROJECT,
        "see enabled APIs and quotas",
        ("serviceusage.services.list", "serviceusage.quotas.get"),
    ),
    Role(
        "roles/serviceusage.apiKeysViewer",
        ON_PROJECT,
        "check how the weather key is restricted",
        ("apikeys.keys.list",),
    ),
    Role(
        "roles/aiplatform.user",
        ON_PROJECT,
        "call Gemini on Vertex AI (GC-11)",
        ("aiplatform.endpoints.predict",),
    ),
    # Not in GC-6's first table: reading a quota's live limit by its id goes
    # through the Cloud Quotas API, whose read permission none of the roles
    # above holds. This role is read-only.
    Role(
        "roles/cloudquotas.viewer",
        ON_PROJECT,
        "read the live limit of each capped quota (GC-10)",
        ("cloudquotas.quotas.get",),
    ),
    Role(
        "roles/secretmanager.viewer",
        ON_SECRET,
        "see that the secret exists and has a version",
        ("secretmanager.secrets.get", "secretmanager.versions.list"),
    ),
    Role(
        "roles/secretmanager.secretAccessor",
        ON_SECRET,
        "read the secret's value at run time",
        ("secretmanager.versions.access",),
    ),
    Role(
        "roles/secretmanager.secretVersionAdder",
        ON_SECRET,
        "store a pasted value (GC-9)",
        ("secretmanager.versions.add",),
    ),
    Role(
        "roles/storage.objectViewer",
        ON_BUCKET,
        "list and read the objects in the bucket",
        ("storage.objects.get", "storage.objects.list"),
    ),
    Role(
        "roles/storage.objectCreator",
        ON_BUCKET,
        "write new objects to the bucket",
        ("storage.objects.create",),
    ),
)

# The permission that shows a secret exists and the account can see it.
SECRET_VISIBLE = "secretmanager.secrets.get"
SECRET_LIST_VERSIONS = "secretmanager.versions.list"
SECRET_ADD_VERSION = "secretmanager.versions.add"
# What calling Gemini on Vertex AI takes.
VERTEX_PREDICT = "aiplatform.endpoints.predict"


def roles_on(granted_on: str, config: GcpConfig | None = None) -> tuple[Role, ...]:
    """The roles granted on one kind of resource.

    With a configuration, `roles/aiplatform.user` is left out when Vertex AI is
    not the chosen Gemini API.
    """
    return tuple(
        role
        for role in ROLES
        if role.granted_on == granted_on
        and not (
            config is not None
            and config.gemini_api != GEMINI_VERTEX_AI
            and VERTEX_PREDICT in role.permissions
        )
    )


def permissions_on(granted_on: str, config: GcpConfig | None = None) -> tuple[str, ...]:
    """Every permission autobot needs on one kind of resource."""
    return tuple(
        permission
        for role in roles_on(granted_on, config)
        for permission in role.permissions
    )


def role_granting(permission: str) -> Role | None:
    """The catalogue role that grants `permission`."""
    return next((role for role in ROLES if permission in role.permissions), None)


# A permission only a broad role holds, and the roles that commonly grant it.
# Holding one means the account could configure the project or reach billing,
# which is what it exists not to do (GC-6).
TOO_BROAD: dict[str, tuple[str, ...]] = {
    "resourcemanager.projects.setIamPolicy": (
        "roles/owner",
        "roles/resourcemanager.projectIamAdmin",
        "roles/iam.securityAdmin",
    ),
    "serviceusage.services.enable": (
        "roles/owner",
        "roles/editor",
        "roles/serviceusage.serviceUsageAdmin",
    ),
    "iam.serviceAccounts.create": (
        "roles/owner",
        "roles/editor",
        "roles/iam.serviceAccountAdmin",
    ),
    "iam.serviceAccountKeys.create": (
        "roles/owner",
        "roles/editor",
        "roles/iam.serviceAccountKeyAdmin",
    ),
    "resourcemanager.projects.createBillingAssignment": (
        "roles/owner",
        "roles/billing.projectManager",
    ),
    "billing.resourceAssociations.create": (
        "roles/billing.admin",
        "roles/billing.user",
    ),
}


@dataclass(frozen=True)
class ExpectedSecret:
    """A secret autobot's configuration expects in Secret Manager (GC-9)."""

    name: str
    # The agent definition that needs it.
    agent: str
    holds: str
    # When the secret is an API key: the display name the key is created
    # with, which is how its restriction is checked without reading its
    # value, and the one API it must be restricted to.
    api_key: str = ""
    api_target: str = ""


# Until `[[agents]]` definitions exist, the expected secrets are listed here.
EXPECTED_SECRETS = (
    ExpectedSecret(
        name="autobot-weather-api-key",
        agent="weather",
        holds="the weather agent's API key (AG-6)",
        api_key="autobot-weather",
        api_target=WEATHER_API,
    ),
)


def expected_secret(name: str) -> ExpectedSecret | None:
    return next((secret for secret in EXPECTED_SECRETS if secret.name == name), None)


def billed_apis(config: GcpConfig) -> tuple[str, ...]:
    """The billed APIs an agent calls: each needs a cap (GC-10)."""
    return (WEATHER_API, config.gemini_api)
