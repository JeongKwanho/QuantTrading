from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.deps import verify_api_key
from backend.api.schemas import (
    CancelOrderResponse, OrderListSchema, OrderType,
    PlaceOrderRequest, OrderSchema,
)
from backend.api.state import app_state
from backend.broker.base import Signal, OrderSide

router = APIRouter(prefix="/orders", tags=["Orders"])


@router.get("", response_model=OrderListSchema, dependencies=[Depends(verify_api_key)])
async def get_orders():
    """미체결 주문 및 최근 주문 내역."""
    open_orders, recent_orders = await app_state.get_orders()
    return OrderListSchema(open_orders=open_orders, recent_orders=recent_orders)


@router.post("", response_model=OrderSchema, dependencies=[Depends(verify_api_key)])
async def place_order(request: PlaceOrderRequest):
    """수동 주문 전송."""
    if not app_state.broker:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Broker not connected",
        )

    signal = Signal(
        symbol=request.symbol,
        direction=OrderSide(request.side.value),
        quantity=request.quantity,
        price=request.price,
        strategy_name="manual",
    )
    order = await app_state.broker.place_order(signal)

    return OrderSchema(
        order_id=order.order_id,
        symbol=order.symbol,
        side=request.side,
        order_type=OrderType.LIMIT if request.price else OrderType.MARKET,
        quantity=order.quantity,
        price=order.price,
        status=order.status.value,
        filled_qty=order.filled_qty,
        avg_fill_price=order.avg_fill_price,
        timestamp=order.timestamp,
    )


@router.post(
    "/cancel/{order_id}",
    response_model=CancelOrderResponse,
    dependencies=[Depends(verify_api_key)],
)
async def cancel_order(order_id: str, symbol: str):
    """주문 취소."""
    success = await app_state.cancel_order(symbol, order_id)
    return CancelOrderResponse(
        order_id=order_id,
        success=success,
        message="Cancelled successfully" if success else "Cancel failed or order already filled",
    )
