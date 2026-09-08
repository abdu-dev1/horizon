"""
Identity and authorization for Horizon, resolved in ONE place: the gateway.

Why here and not in each backend: the gateway is the only process reachable
from outside, and both engines listen on loopback only. Putting the policy at
the single entry point means the two backends need no auth code at all, and
there is exactly one list of admin-only paths to keep correct rather than two
implementations that can drift apart.

How identity arrives
--------------------
In production, App Service Authentication ("Easy Auth") sits in FRONT of the
container: it completes the Entra ID sign-in itself and injects the verified
user into request headers. Requests never reach this process unauthenticated,
and Azure strips any client-supplied copy of those headers before injecting its
own -- which is what makes them trustworthy here. This app does not validate
tokens, because it never sees one.

Two modes, and the default is the safe one
------------------------------------------
    HORIZON_AUTH_MODE=easyauth   trust the Easy Auth headers; 401 without them
    HORIZON_AUTH_MODE=dev        no sign-in; act as HORIZON_DEV_USER

`dev` is the module default so that a plain `python gateway/run_app.py` works
with no configuration, but the Dockerfile pins `easyauth` -- the container is
the thing that actually gets deployed, so the artifact you ship is secure
regardless of what the developer default is.

In `easyauth` mode a request with no principal header is REJECTED rather than
treated as anonymous. A missing header there does not mean "a guest"; it means
Easy Auth is not actually in front of the container, i.e. the app is exposed.
Failing closed turns that misconfiguration into an obvious outage instead of a
silent data leak.

Spoofing
--------
The gateway strips every inbound `X-Horizon-*` header from the client before
injecting its own (see main.py). Without that, anyone could add
`X-Horizon-Is-Admin: 1` and self-promote, since the engines trust it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Set by Easy Auth in front of the container.
EASY_AUTH_USER_HEADER = "x-ms-client-principal-name"

# Set by THIS process for the engines behind it. Any client-supplied header
# with the `x-horizon-` prefix is stripped before these are injected.
INJECT_PREFIX = "x-horizon-"
INJECT_USER_HEADER = "x-horizon-user"
INJECT_ADMIN_HEADER = "x-horizon-is-admin"

AUTH_MODE = os.environ.get("HORIZON_AUTH_MODE", "dev").strip().lower()
DEV_USER = os.environ.get("HORIZON_DEV_USER", "dev@localhost").strip()


def _admin_emails() -> set[str]:
    """Read on every call, not cached at import: an App Service setting change
    restarts the app anyway, but this also makes the value testable and means a
    typo'd setting can be fixed without reasoning about import order."""
    raw = os.environ.get("HORIZON_ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


@dataclass(frozen=True)
class Identity:
    email: str
    is_admin: bool


def resolve(headers) -> Identity | None:
    """The signed-in user, or None if the request must be rejected.

    `headers` is any case-insensitive mapping (Starlette's request.headers).
    """
    if AUTH_MODE == "dev":
        # Admin by default in dev. Withholding the role here would buy no
        # security -- dev mode already means "no sign-in at all" -- while
        # making the admin pages unreachable on localhost, which is where they
        # get built. The boundary is the MODE, and the Dockerfile pins
        # easyauth, so the deployed artifact never takes this branch.
        # Set HORIZON_DEV_ADMIN=0 to check the standard-user view locally.
        dev_admin = os.environ.get("HORIZON_DEV_ADMIN", "1") != "0"
        return Identity(email=DEV_USER, is_admin=dev_admin)

    email = (headers.get(EASY_AUTH_USER_HEADER) or "").strip()
    if not email:
        return None
    return Identity(email=email, is_admin=_is_admin(email))


def _is_admin(email: str) -> bool:
    admins = _admin_emails()
    # An empty allowlist grants nothing. The alternative -- treating "no
    # admins configured" as "everyone is an admin" -- would make a forgotten
    # app setting silently expose retraining and data-quality internals.
    return bool(email) and email.strip().lower() in admins


# ---------------------------------------------------------------------------
# Authorization policy: which paths require an admin.
#
# Paths are matched AFTER the gateway's own /nb-app prefix handling, against
# the full incoming path, so the New Business equivalents are listed
# separately with their prefix. Keep this list and NB_PAGES/RENEWAL_PAGES in
# frontend/src/App.jsx in agreement -- hiding a page in the UI is presentation,
# not access control; this is the part that actually enforces it.
# ---------------------------------------------------------------------------
ADMIN_PREFIXES: tuple[str, ...] = (
    # Anything added under this prefix is admin-only by construction, so a
    # future endpoint cannot be left unprotected by forgetting this list.
    "/api/admin/",
    "/nb-app/api/admin/",

    # Model internals -- the Model Performance pages (both products).
    "/api/model/",
    "/nb-app/api/model/",

    # Data Quality pages (both products).
    "/api/data-quality",
    "/nb-app/api/data-quality",

    # Model Maintenance: retraining and the data-upload workflow.
    "/api/retrain",
    "/nb-app/api/retrain",
    "/api/upload/",
    "/api/upload",
)

# Destructive or bulk-mutating operations that are not on an admin-only page
# but should not be available to every signed-in user either.
ADMIN_EXACT: frozenset[str] = frozenset({
    "/api/groups/move-decided-to-database",
})


def requires_admin(path: str, method: str) -> bool:
    if path in ADMIN_EXACT:
        return True
    if any(path.startswith(p) for p in ADMIN_PREFIXES):
        return True
    # Deleting a renewal group is destructive; reading one is not.
    if method == "DELETE" and path.startswith("/api/groups/"):
        return True
    return False


# Paths served without any identity at all.
PUBLIC_PATHS: frozenset[str] = frozenset({
    # The platform's liveness probe. Must stay unauthenticated: an
    # authenticated health path fails the probe, which the platform reads as a
    # dead container and restarts in a loop.
    "/healthz",
})
