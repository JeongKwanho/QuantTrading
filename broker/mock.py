import uuid
from datetime import datetime
from dataclasses import dataclass, field

from backend.broker.base import (
    Balance, BaseBroker, Fill, Order,
    OrderSide, OrderStatus, OrderType, Position, Signal,
)


# Binance Futures 기본 수수료
TAKER_FEE = 0.0004   # 시장가 0.04%
MAKER_FEE = 0.0002   # 지정가 0.02%
MAINTENANCE_MARGIN_RATE = 0.004  # 유지증거금율 0.4% (Binance 기본)


@dataclass
class _MockPosition:
    symbol: str
    side: OrderSide
    size: float
    entry_price: float
    leverage: int
    margin: float          # 사용된 증거금
    liquidation_price: float


def _calc_liquidation_price(side: OrderSide, entry: float, leverage: int) -> float:
    """청산가 계산."""
    if side == OrderSide.BUY:
        return entry * (1 - 1 / leverage + MAINTENANCE_MARGIN_RATE)
    else:
        return entry * (1 + 1 / leverage - MAINTENANCE_MARGIN_RATE)


class MockBroker(BaseBroker):
    """
    레버리지 + 수수료를 반영한 백테스팅용 가상 브로커.
    실제 거래소 연결 없이 잔고/포지션/주문을 인메모리로 관리한다.
    """

    def __init__(
        self,
        initial_balance: float = 10000.0,
        leverage: int = 1,
        slippage: float = 0.0,
        taker_fee: float = TAKER_FEE,
        maker_fee: float = MAKER_FEE,
    ) -> None:
        super().__init__()
        self._initial_balance = initial_balance
        self._leverage = leverage
        self._slippage = slippage
        self._taker_fee = taker_fee
        self._maker_fee = maker_fee

        self._balance: float = initial_balance
        self._positions: dict[str, _MockPosition] = {}
        self._orders: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._current_prices: dict[str, float] = {}
        self._funding_fees: float = 0.0

    # ── 연결 관리 ─────────────────────────────────────────────────────────

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    # ── 가격 업데이트 (엔진이 캔들마다 호출) ─────────────────────────────

    def update_price(self, symbol: str, price: float) -> None:
        """현재가 업데이트 — 청산 여부도 함께 체크."""
        self._current_prices[symbol] = price
        self._check_liquidation(symbol, price)

    # ── 계좌 정보 ─────────────────────────────────────────────────────────

    async def get_balances(self) -> list[Balance]:
        locked = sum(p.margin for p in self._positions.values())
        return [Balance(asset="USDT", free=self._balance, locked=locked)]

    async def get_positions(self) -> list[Position]:
        result = []
        for p in self._positions.values():
            current = self._current_prices.get(p.symbol, p.entry_price)
            upnl = self._calc_upnl(p, current)
            result.append(Position(
                symbol=p.symbol,
                side=p.side,
                size=p.size,
                entry_price=p.entry_price,
                unrealized_pnl=upnl,
                leverage=p.leverage,
            ))
        return result

    # ── 주문 관리 ─────────────────────────────────────────────────────────

    async def place_order(self, signal: Signal) -> Order:
        order_id = str(uuid.uuid4())[:8]
        current_price = self._current_prices.get(signal.symbol, signal.price or 0)

        order = Order(
            order_id=order_id,
            symbol=signal.symbol,
            side=signal.direction,
            order_type=signal.order_type,
            quantity=signal.quantity,
            price=signal.price,
            status=OrderStatus.SUBMITTED,
            timestamp=datetime.utcnow(),
        )
        self._orders[order_id] = order

        # 시장가는 즉시 체결, 지정가는 pending 상태로 대기
        if signal.order_type == OrderType.MARKET:
            fill_price = current_price * (1 + self._slippage if signal.direction == OrderSide.BUY else 1 - self._slippage)
            self._execute_fill(order, fill_price)

        return self._orders[order_id]

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if order and order.status == OrderStatus.SUBMITTED:
            order.status = OrderStatus.CANCELLED
            return True
        return False

    async def get_order(self, symbol: str, order_id: str) -> Order:
        return self._orders[order_id]

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        return [
            o for o in self._orders.values()
            if o.status == OrderStatus.SUBMITTED
            and (symbol is None or o.symbol == symbol)
        ]

    # ── 시장 데이터 ───────────────────────────────────────────────────────

    async def get_ticker(self, symbol: str) -> dict:
        price = self._current_prices.get(symbol, 0.0)
        return {"symbol": symbol, "price": price, "bid": price, "ask": price, "timestamp": datetime.utcnow()}

    async def get_orderbook(self, symbol: str, depth: int = 10) -> dict:
        return {"bids": [], "asks": []}

    async def get_ohlcv(self, symbol: str, interval: str, limit: int = 200) -> list[dict]:
        return []

    # ── 지정가 체결 체크 (캔들마다 호출) ─────────────────────────────────

    def check_limit_orders(self, symbol: str, high: float, low: float) -> None:
        """해당 캔들의 고가/저가 범위 안에서 지정가 주문 체결 처리."""
        for order in list(self._orders.values()):
            if order.symbol != symbol or order.status != OrderStatus.SUBMITTED:
                continue
            if order.order_type != OrderType.LIMIT or order.price is None:
                continue

            hit = (order.side == OrderSide.BUY and order.price >= low) or \
                  (order.side == OrderSide.SELL and order.price <= high)

            if hit:
                self._execute_fill(order, order.price)

    # ── 성과 조회 ─────────────────────────────────────────────────────────

    def get_total_equity(self) -> float:
        """현재 총 자산 = 잔고 + 증거금 + 미실현 손익."""
        total = self._balance
        for p in self._positions.values():
            current = self._current_prices.get(p.symbol, p.entry_price)
            total += p.margin + self._calc_upnl(p, current)
        return total

    def apply_funding_rate(self, symbol: str, funding_rate: float) -> float:
        """Apply one perpetual futures funding event and return the fee paid.

        Positive funding means longs pay shorts. Negative funding means longs receive.
        """
        pos = self._positions.get(symbol)
        if not pos:
            return 0.0

        current = self._current_prices.get(symbol, pos.entry_price)
        notional = current * pos.size
        side_multiplier = 1 if pos.side == OrderSide.BUY else -1
        fee = notional * funding_rate * side_multiplier
        self._balance -= fee
        self._funding_fees += fee
        return fee

    def get_funding_fees(self) -> float:
        return self._funding_fees

    def get_fills(self) -> list[Fill]:
        return list(self._fills)

    def reset(self) -> None:
        """백테스트 초기화."""
        self._balance = self._initial_balance
        self._positions.clear()
        self._orders.clear()
        self._fills.clear()
        self._current_prices.clear()
        self._funding_fees = 0.0

    # ── 내부 계산 ─────────────────────────────────────────────────────────

    def _execute_fill(self, order: Order, fill_price: float) -> None:
        fee_rate = self._taker_fee if order.order_type == OrderType.MARKET else self._maker_fee
        fee = fill_price * order.quantity * fee_rate
        notional = fill_price * order.quantity
        margin = notional / self._leverage

        existing = self._positions.get(order.symbol)

        if existing is not None and existing.side != order.side:
            # 반대 포지션 → 청산
            self._close_position(order, fill_price, fee)
        else:
            # 신규 포지션 열기 (롱 BUY or 숏 SELL)
            cost = margin + fee
            if cost > self._balance:
                order.status = OrderStatus.REJECTED
                return
            self._balance -= cost
            self._open_position(order, fill_price, margin)

        order.status = OrderStatus.FILLED
        order.filled_qty = order.quantity
        order.avg_fill_price = fill_price

        fill = Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            price=fill_price,
            quantity=order.quantity,
            fee=fee,
            fee_asset="USDT",
            timestamp=datetime.utcnow(),
        )
        self._fills.append(fill)

        if self._on_fill_callback:
            self._on_fill_callback(fill)

    def _open_position(self, order: Order, price: float, margin: float) -> None:
        existing = self._positions.get(order.symbol)
        if existing is not None and existing.side == order.side:
            total_qty = existing.size + order.quantity
            existing.entry_price = (
                existing.entry_price * existing.size + price * order.quantity
            ) / total_qty
            existing.size = total_qty
            existing.margin += margin
            existing.liquidation_price = _calc_liquidation_price(
                existing.side,
                existing.entry_price,
                self._leverage,
            )
            return

        liq = _calc_liquidation_price(order.side, price, self._leverage)
        self._positions[order.symbol] = _MockPosition(
            symbol=order.symbol,
            side=order.side,
            size=order.quantity,
            entry_price=price,
            leverage=self._leverage,
            margin=margin,
            liquidation_price=liq,
        )

    def _close_position(self, order: Order, price: float, fee: float) -> None:
        pos = self._positions.get(order.symbol)
        if not pos:
            return
        close_qty = min(order.quantity, pos.size)
        close_ratio = close_qty / pos.size if pos.size else 0.0
        released_margin = pos.margin * close_ratio

        if pos.side == OrderSide.BUY:
            pnl = (price - pos.entry_price) * close_qty
        else:
            pnl = (pos.entry_price - price) * close_qty

        self._balance += released_margin + pnl - fee
        pos.size -= close_qty
        pos.margin -= released_margin

        if pos.size <= 1e-12:
            del self._positions[order.symbol]

    def _calc_upnl(self, pos: _MockPosition, current_price: float) -> float:
        if pos.side == OrderSide.BUY:
            return (current_price - pos.entry_price) * pos.size
        else:
            return (pos.entry_price - current_price) * pos.size

    def _check_liquidation(self, symbol: str, price: float) -> None:
        pos = self._positions.get(symbol)
        if not pos:
            return
        liquidated = (
            pos.side == OrderSide.BUY and price <= pos.liquidation_price
        ) or (
            pos.side == OrderSide.SELL and price >= pos.liquidation_price
        )
        if liquidated:
            self._balance -= pos.margin   # 증거금 전액 손실
            del self._positions[symbol]
