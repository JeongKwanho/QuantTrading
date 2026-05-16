from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.deps import verify_api_key
from backend.api.schemas import (
    StrategyListSchema, StrategySchema, StrategyStatus,
    ToggleStrategyResponse, UpdateParamsRequest, UpdateParamsResponse,
)
from backend.api.state import app_state

router = APIRouter(prefix="/strategy", tags=["Strategy"])


@router.get("", response_model=StrategyListSchema, dependencies=[Depends(verify_api_key)])
async def get_strategies():
    """로드된 전략 목록과 현재 상태."""
    strategies = app_state.get_strategies()
    return StrategyListSchema(
        strategies=strategies,
        total=len(strategies),
        active_count=sum(1 for s in strategies if s.status == StrategyStatus.ON),
    )


@router.post(
    "/{name}/toggle",
    response_model=ToggleStrategyResponse,
    dependencies=[Depends(verify_api_key)],
)
async def toggle_strategy(name: str):
    """전략 ON/OFF 전환."""
    strategy = app_state.toggle_strategy(name)
    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy '{name}' not found",
        )
    return ToggleStrategyResponse(
        name=name,
        status=strategy.status,
        message=f"Strategy '{name}' is now {strategy.status.value}",
    )


@router.post(
    "/{name}/params",
    response_model=UpdateParamsResponse,
    dependencies=[Depends(verify_api_key)],
)
async def update_params(name: str, request: UpdateParamsRequest):
    """전략 파라미터 업데이트."""
    strategy = app_state.update_params(name, request.parameters)
    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy '{name}' not found",
        )
    return UpdateParamsResponse(
        name=name,
        parameters=strategy.parameters,
        message=f"Parameters updated for '{name}'",
    )
