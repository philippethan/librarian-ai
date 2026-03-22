import logging
import os

from fastapi import Header, HTTPException, Query

logger = logging.getLogger(__name__)

_API_KEY = os.getenv("API_KEY", "")

if not _API_KEY:
    logger.warning("API_KEY is not set — authentication is disabled.")


async def require_auth(
    x_api_key: str = Header(default="", alias="X-API-Key"),
    api_key: str = Query(default=""),
):
    if not _API_KEY:
        return  # auth disabled
    provided = x_api_key or api_key
    if provided != _API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
