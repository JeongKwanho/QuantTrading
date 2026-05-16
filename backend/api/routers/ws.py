import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.api.state import app_state

router = APIRouter(prefix="/ws", tags=["WebSocket"])


@router.websocket("/market")
async def ws_market(websocket: WebSocket):
    """실시간 시세 스트림. Query param: symbol (예: BTCUSDT)."""
    await websocket.accept()
    symbol = websocket.query_params.get("symbol", "BTCUSDT")
    try:
        while True:
            if app_state.broker:
                ticker = await app_state.broker.get_ticker(symbol)
            else:
                ticker = {
                    "symbol": symbol,
                    "price": 65000.0,
                    "bid": 64999.0,
                    "ask": 65001.0,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            await websocket.send_text(json.dumps({
                "type": "ticker",
                **ticker,
                "timestamp": datetime.utcnow().isoformat(),
            }, default=str))
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass


@router.websocket("/portfolio")
async def ws_portfolio(websocket: WebSocket):
    """실시간 포트폴리오 업데이트 스트림."""
    await websocket.accept()
    try:
        while True:
            portfolio = await app_state.get_portfolio()
            await websocket.send_text(json.dumps({
                "type": "portfolio",
                "total_balance": portfolio.total_balance,
                "unrealized_pnl": portfolio.unrealized_pnl,
                "daily_pnl": portfolio.daily_pnl,
                "positions": [p.model_dump() for p in portfolio.positions],
                "timestamp": datetime.utcnow().isoformat(),
            }, default=str))
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass


@router.websocket("/alerts")
async def ws_alerts(websocket: WebSocket):
    """리스크 알림 및 체결 알림 스트림."""
    await websocket.accept()
    try:
        while True:
            risk = app_state.get_risk()
            alerts = []

            if risk.is_trading_halted:
                alerts.append({
                    "type": "alert",
                    "level": "CRITICAL",
                    "message": "Trading halted: drawdown limit reached",
                    "timestamp": datetime.utcnow().isoformat(),
                })
            if risk.daily_loss >= risk.daily_loss_limit * 0.8:
                alerts.append({
                    "type": "alert",
                    "level": "WARNING",
                    "message": f"Daily loss at {risk.daily_loss:.1f} USDT (limit: {risk.daily_loss_limit:.1f})",
                    "timestamp": datetime.utcnow().isoformat(),
                })

            for alert in alerts:
                await websocket.send_text(json.dumps(alert, default=str))

            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
