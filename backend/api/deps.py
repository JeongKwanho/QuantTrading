from fastapi import Header, HTTPException, status
from backend.config import settings


async def verify_api_key(x_api_key: str = Header(...)) -> None:
    """API Key 인증 — 모든 라우터에 Depends()로 주입."""
    if x_api_key != settings.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
        )
