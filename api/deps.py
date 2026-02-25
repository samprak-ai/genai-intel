"""
Shared FastAPI dependencies
Provides a single DatabaseClient instance and token-based auth verification.
"""

import os
import secrets
from functools import lru_cache

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.database import DatabaseClient


@lru_cache(maxsize=1)
def get_db() -> DatabaseClient:
    """
    Singleton database client — instantiated once per process.
    Inject with: db: DatabaseClient = Depends(get_db)
    """
    return DatabaseClient()


# ---------------------------------------------------------------------------
# Token auth
# ---------------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=False)


def verify_token(
    creds: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    """
    Bearer-token guard for all protected API routes.

    Reads DASHBOARD_SECRET from env.  If the env var is not set (local dev),
    all requests pass through — no token required.  In production, any request
    without a matching token receives HTTP 401.

    Usage:
        # Per-route
        @router.get("/endpoint")
        def my_endpoint(_: None = Depends(verify_token)): ...

        # Whole router (preferred — applied in main.py)
        app.include_router(router, dependencies=[Depends(verify_token)])
    """
    expected = os.getenv("DASHBOARD_SECRET")
    if not expected:
        return  # no secret configured → open access (local dev)

    if creds is None or not secrets.compare_digest(creds.credentials, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing token",
            headers={"WWW-Authenticate": "Bearer"},
        )
