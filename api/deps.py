"""
Shared FastAPI dependencies
Provides a single DatabaseClient instance and (later) auth verification.
"""

from functools import lru_cache
from app.core.database import DatabaseClient


@lru_cache(maxsize=1)
def get_db() -> DatabaseClient:
    """
    Singleton database client — instantiated once per process.
    Inject with: db: DatabaseClient = Depends(get_db)
    """
    return DatabaseClient()
