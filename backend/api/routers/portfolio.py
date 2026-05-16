from fastapi import APIRouter, Depends

from backend.api.deps import verify_api_key
from backend.api.schemas import PortfolioSchema, RiskSchema
from backend.api.state import app_state

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


@router.get("", response_model=PortfolioSchema, dependencies=[Depends(verify_api_key)])
async def get_portfolio():
    """현재 포트폴리오 상태 (잔고, 포지션, 손익)."""
    return await app_state.get_portfolio()


@router.get("/risk", response_model=RiskSchema, dependencies=[Depends(verify_api_key)])
async def get_risk():
    """현재 리스크 지표 (드로다운, 일일 손실 한도 등)."""
    return app_state.get_risk()
