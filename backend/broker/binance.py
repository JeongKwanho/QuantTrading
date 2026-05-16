import asyncio
import logging
from datetime import datetime

from binance import AsyncClient, BinanceSocketManager

from .base import (
    Balance, BaseBroker, Fill, Order, OrderSide,
    OrderStatus, OrderType, Position, Signal,
)

logger = logging.getLogger(__name__)


class BinanceBroker(BaseBroker):
    """Binance USDT-M Futures broker implementation."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
    ) -> None:
        super().__init__()
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._client: AsyncClient | None = None
        self._bsm: BinanceSocketManager | None = None
        self._ws_tasks: list[asyncio.Task] = []

    # ── 연결 관리 ─────────────────────────────────────────────────────────

    async def connect(self) -> None:
        self._client = await AsyncClient.create(
            api_key=self._api_key,
            api_secret=self._api_secret,
            testnet=self._testnet,
        )
        self._bsm = BinanceSocketManager(self._client)
        logger.info("BinanceBroker connected (testnet=%s)", self._testnet)

    async def disconnect(self) -> None:
        for task in self._ws_tasks:
            task.cancel()
        self._ws_tasks.clear()

        if self._client:
            await self._client.close_connection()
            self._client = None

        logger.info("BinanceBroker disconnected")

    # ── 계좌 정보 ─────────────────────────────────────────────────────────

    async def get_balances(self) -> list[Balance]:
        raw = await self._client.futures_account_balance()
        return [
            Balance(
                asset=b["asset"],
                free=float(b["availableBalance"]),
                locked=float(b["balance"]) - float(b["availableBalance"]),
            )
            for b in raw
            if float(b["balance"]) > 0
        ]

    async def get_positions(self) -> list[Position]:
        raw = await self._client.futures_position_information()
        positions = []
        for p in raw:
            size = float(p["positionAmt"])
            if size == 0:
                continue
            positions.append(Position(
                symbol=p["symbol"],
                side=OrderSide.BUY if size > 0 else OrderSide.SELL,
                size=abs(size),
                entry_price=float(p["entryPrice"]),
                unrealized_pnl=float(p["unRealizedProfit"]),
                leverage=int(p["leverage"]),
            ))
        return positions

    # ── 주문 관리 ─────────────────────────────────────────────────────────

    async def place_order(self, signal: Signal) -> Order:
        params: dict = {
            "symbol": signal.symbol,
            "side": signal.direction.value,
            "type": signal.order_type.value,
            "quantity": signal.quantity,
        }
        if signal.order_type == OrderType.LIMIT:
            params["price"] = signal.price
            params["timeInForce"] = "GTC"

        raw = await self._client.futures_create_order(**params)

        order = self._parse_order(raw)
        logger.info("Order placed: %s %s %s qty=%s",
                    order.order_id, order.side.value, order.symbol, order.quantity)
        return order

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        try:
            await self._client.futures_cancel_order(
                symbol=symbol,
                orderId=order_id,
            )
            return True
        except Exception as e:
            logger.warning("Cancel failed (order_id=%s): %s", order_id, e)
            return False

    async def get_order(self, symbol: str, order_id: str) -> Order:
        raw = await self._client.futures_get_order(
            symbol=symbol,
            orderId=order_id,
        )
        return self._parse_order(raw)

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        params = {"symbol": symbol} if symbol else {}
        raw = await self._client.futures_get_open_orders(**params)
        return [self._parse_order(o) for o in raw]

    # ── 시장 데이터 ───────────────────────────────────────────────────────

    async def get_ticker(self, symbol: str) -> dict:
        raw = await self._client.futures_symbol_ticker(symbol=symbol)
        book = await self._client.futures_order_book(symbol=symbol, limit=5)
        return {
            "symbol": symbol,
            "price": float(raw["price"]),
            "bid": float(book["bids"][0][0]) if book["bids"] else None,
            "ask": float(book["asks"][0][0]) if book["asks"] else None,
            "timestamp": datetime.utcnow(),
        }

    async def get_orderbook(self, symbol: str, depth: int = 10) -> dict:
        raw = await self._client.futures_order_book(symbol=symbol, limit=depth)
        return {
            "bids": [[float(p), float(q)] for p, q in raw["bids"]],
            "asks": [[float(p), float(q)] for p, q in raw["asks"]],
        }

    async def get_ohlcv(
        self,
        symbol: str,
        interval: str,
        limit: int = 200,
    ) -> list[dict]:
        raw = await self._client.futures_klines(
            symbol=symbol,
            interval=interval,
            limit=limit,
        )
        return [
            {
                "timestamp": datetime.utcfromtimestamp(k[0] / 1000),
                "open":   float(k[1]),
                "high":   float(k[2]),
                "low":    float(k[3]),
                "close":  float(k[4]),
                "volume": float(k[5]),
            }
            for k in raw
        ]

    # ── WebSocket 스트림 ──────────────────────────────────────────────────

    async def subscribe_ticker(self, symbol: str) -> None:
        """실시간 체결가 스트림 구독 — on_ticker 콜백으로 전달."""
        async def _stream():
            async with self._bsm.futures_symbol_mark_price_socket(symbol) as stream:
                while True:
                    msg = await stream.recv()
                    if self._on_ticker_callback and msg.get("e") == "markPriceUpdate":
                        self._on_ticker_callback({
                            "symbol":    msg["s"],
                            "price":     float(msg["p"]),
                            "timestamp": datetime.utcfromtimestamp(msg["T"] / 1000),
                        })

        task = asyncio.create_task(_stream())
        self._ws_tasks.append(task)
        logger.info("Subscribed ticker stream: %s", symbol)

    async def subscribe_user_stream(self) -> None:
        """체결/주문 이벤트 스트림 구독 — on_fill 콜백으로 전달."""
        async def _stream():
            async with self._bsm.futures_user_socket() as stream:
                while True:
                    msg = await stream.recv()
                    if msg.get("e") == "ORDER_TRADE_UPDATE":
                        order_data = msg["o"]
                        if order_data["X"] == "FILLED":
                            fill = Fill(
                                order_id=str(order_data["i"]),
                                symbol=order_data["s"],
                                side=OrderSide(order_data["S"]),
                                price=float(order_data["ap"]),   # avg fill price
                                quantity=float(order_data["z"]), # filled qty
                                fee=float(order_data["n"]),
                                fee_asset=order_data["N"] or "USDT",
                                timestamp=datetime.utcfromtimestamp(msg["T"] / 1000),
                            )
                            if self._on_fill_callback:
                                self._on_fill_callback(fill)

        task = asyncio.create_task(_stream())
        self._ws_tasks.append(task)
        logger.info("Subscribed user stream")

    # ── 내부 유틸 ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_order(raw: dict) -> Order:
        status_map = {
            "NEW":              OrderStatus.SUBMITTED,
            "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
            "FILLED":           OrderStatus.FILLED,
            "CANCELED":         OrderStatus.CANCELLED,
            "REJECTED":         OrderStatus.REJECTED,
            "EXPIRED":          OrderStatus.CANCELLED,
        }
        return Order(
            order_id=str(raw["orderId"]),
            symbol=raw["symbol"],
            side=OrderSide(raw["side"]),
            order_type=OrderType(raw["type"]),
            quantity=float(raw["origQty"]),
            price=float(raw["price"]) if float(raw.get("price", 0)) > 0 else None,
            status=status_map.get(raw["status"], OrderStatus.PENDING),
            filled_qty=float(raw.get("executedQty", 0)),
            avg_fill_price=float(raw["avgPrice"]) if float(raw.get("avgPrice", 0)) > 0 else None,
            timestamp=datetime.utcfromtimestamp(raw["time"] / 1000) if "time" in raw else datetime.utcnow(),
        )
