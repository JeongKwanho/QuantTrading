from datetime import datetime

from backend.broker.base import BaseBroker
from backend.api.schemas import (
    BalanceSchema, OrderSchema, OrderSide, OrderStatus, OrderType,
    PortfolioSchema, PositionSchema, RiskSchema,
    StrategySchema, StrategyStatus, StrategyTimeframe,
)


class AppState:
    """
    애플리케이션 전역 상태.
    브로커 인스턴스를 보유하고, 아직 엔진/DB가 없는 동안
    인메모리 Mock 데이터를 제공한다.
    엔진이 완성되면 Mock 메서드들을 실제 엔진 호출로 교체한다.
    """

    def __init__(self) -> None:
        self.broker: BaseBroker | None = None
        self._mock_orders: list[OrderSchema] = self._seed_orders()
        self._mock_strategies: list[StrategySchema] = self._seed_strategies()

    # ── 포트폴리오 ────────────────────────────────────────────────────────

    async def get_portfolio(self) -> PortfolioSchema:
        if self.broker:
            balances = await self.broker.get_balances()
            positions = await self.broker.get_positions()
            usdt = next((b for b in balances if b.asset == "USDT"), None)
            total = usdt.total if usdt else 0.0
            available = usdt.free if usdt else 0.0
            upnl = sum(p.unrealized_pnl for p in positions)

            return PortfolioSchema(
                total_balance=total,
                available_balance=available,
                unrealized_pnl=upnl,
                daily_pnl=0.0,
                daily_pnl_pct=0.0,
                positions=[
                    PositionSchema(
                        symbol=p.symbol,
                        side=OrderSide(p.side.value),
                        size=p.size,
                        entry_price=p.entry_price,
                        current_price=p.entry_price,
                        unrealized_pnl=p.unrealized_pnl,
                        unrealized_pnl_pct=(p.unrealized_pnl / (p.entry_price * p.size)) * 100
                            if p.entry_price and p.size else 0.0,
                        leverage=p.leverage,
                    )
                    for p in positions
                ],
                updated_at=datetime.utcnow(),
            )

        # Mock 데이터
        return PortfolioSchema(
            total_balance=10000.0,
            available_balance=7500.0,
            unrealized_pnl=150.0,
            daily_pnl=85.0,
            daily_pnl_pct=0.85,
            positions=[
                PositionSchema(
                    symbol="BTCUSDT",
                    side=OrderSide.BUY,
                    size=0.05,
                    entry_price=62000.0,
                    current_price=65000.0,
                    unrealized_pnl=150.0,
                    unrealized_pnl_pct=4.84,
                    leverage=10,
                )
            ],
            updated_at=datetime.utcnow(),
        )

    # ── 주문 ──────────────────────────────────────────────────────────────

    async def get_orders(self) -> tuple[list[OrderSchema], list[OrderSchema]]:
        if self.broker:
            open_raw = await self.broker.get_open_orders()
            open_orders = [self._to_order_schema(o) for o in open_raw]
            recent = [o for o in self._mock_orders if o.status != OrderStatus.SUBMITTED]
            return open_orders, recent

        open_orders = [o for o in self._mock_orders if o.status == OrderStatus.SUBMITTED]
        recent = [o for o in self._mock_orders if o.status != OrderStatus.SUBMITTED]
        return open_orders, recent

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        if self.broker:
            return await self.broker.cancel_order(symbol, order_id)

        for order in self._mock_orders:
            if order.order_id == order_id:
                order.status = OrderStatus.CANCELLED
                return True
        return False

    # ── 전략 ──────────────────────────────────────────────────────────────

    def get_strategies(self) -> list[StrategySchema]:
        return self._mock_strategies

    def toggle_strategy(self, name: str) -> StrategySchema | None:
        for s in self._mock_strategies:
            if s.name == name:
                s.status = StrategyStatus.OFF if s.status == StrategyStatus.ON else StrategyStatus.ON
                return s
        return None

    def update_params(self, name: str, params: dict) -> StrategySchema | None:
        for s in self._mock_strategies:
            if s.name == name:
                s.parameters.update(params)
                return s
        return None

    # ── 리스크 ────────────────────────────────────────────────────────────

    def get_risk(self) -> RiskSchema:
        return RiskSchema(
            drawdown_pct=2.5,
            drawdown_limit_pct=15.0,
            daily_loss=0.0,
            daily_loss_limit=500.0,
            open_order_count=1,
            max_open_orders=10,
            is_trading_halted=False,
        )

    # ── 내부 유틸 ─────────────────────────────────────────────────────────

    @staticmethod
    def _to_order_schema(order) -> OrderSchema:
        return OrderSchema(
            order_id=order.order_id,
            symbol=order.symbol,
            side=OrderSide(order.side.value),
            order_type=OrderType(order.order_type.value),
            quantity=order.quantity,
            price=order.price,
            status=OrderStatus(order.status.value),
            filled_qty=order.filled_qty,
            avg_fill_price=order.avg_fill_price,
            timestamp=order.timestamp,
        )

    @staticmethod
    def _seed_orders() -> list[OrderSchema]:
        return [
            OrderSchema(
                order_id="mock-001",
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=0.05,
                price=62000.0,
                status=OrderStatus.FILLED,
                filled_qty=0.05,
                avg_fill_price=62000.0,
                timestamp=datetime(2026, 5, 17, 9, 0, 0),
            ),
            OrderSchema(
                order_id="mock-002",
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=0.02,
                price=64000.0,
                status=OrderStatus.SUBMITTED,
                filled_qty=0.0,
                avg_fill_price=None,
                timestamp=datetime(2026, 5, 17, 10, 30, 0),
            ),
        ]

    @staticmethod
    def _seed_strategies() -> list[StrategySchema]:
        return [
            StrategySchema(
                name="example_swing",
                timeframe=StrategyTimeframe.SWING,
                symbols=["BTCUSDT"],
                status=StrategyStatus.ON,
                parameters={"lookback": 20, "stop_loss_pct": 0.03},
                last_signal_at=None,
                description="Example swing strategy placeholder",
            ),
        ]


# 앱 전역에서 공유되는 단일 인스턴스
app_state = AppState()
