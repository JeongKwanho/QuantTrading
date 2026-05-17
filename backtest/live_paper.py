import asyncio
import logging
from datetime import datetime

from binance import AsyncClient, BinanceSocketManager

from broker.mock import MockBroker
from strategies.base import MarketData
from strategies.groups import BaseGroup
from backend.broker.base import Signal as BrokerSignal, OrderSide

logger = logging.getLogger(__name__)


class LivePaperBacktest:
    """
    실제 Binance WebSocket에서 실시간 캔들을 받아 전략 그룹을 검증한다.
    시간봉별 스트림을 병렬로 열고 확정 캔들만 그룹에 전달.
    주문은 MockBroker로 처리 — 실제 돈이 나가지 않는다.
    """

    def __init__(
        self,
        group: BaseGroup,
        symbol: str,
        initial_balance: float = 10000.0,
        leverage: int = 1,
        slippage: float = 0.0,
        api_key: str = "",
        api_secret: str = "",
    ) -> None:
        self.group = group
        self.symbol = symbol
        self.broker = MockBroker(
            initial_balance=initial_balance,
            leverage=leverage,
            slippage=slippage,
        )
        self._api_key = api_key
        self._api_secret = api_secret
        self._running = False
        self._equity_curve: list[dict] = []
        self._primary = group.primary_timeframe
        self._lock = asyncio.Lock()  # equity curve / broker 동시 접근 방지

        def _on_fill(fill):
            for s in group.all_strategies():
                s.on_fill(fill)
        self.broker.set_on_fill(_on_fill)

    async def start(self) -> None:
        """실시간 페이퍼 트레이딩 시작. stop() 또는 Ctrl+C로 중단."""
        client = await AsyncClient.create(
            api_key=self._api_key,
            api_secret=self._api_secret,
        )
        bsm = BinanceSocketManager(client)
        self._running = True
        self.group.reset()

        logger.info(
            "Live paper backtest started — %s timeframes=%s (leverage=%dx)",
            self.symbol, self.group.timeframes, self.broker._leverage,
        )

        try:
            await asyncio.gather(
                *[self._listen(bsm, tf) for tf in self.group.timeframes]
            )
        except asyncio.CancelledError:
            pass
        finally:
            await client.close_connection()
            logger.info("Live paper backtest stopped")

    def stop(self) -> None:
        """외부에서 중단 요청."""
        self._running = False

    def get_result(self) -> dict:
        """현재까지의 성과 반환."""
        from backtest.report import BacktestReport
        report = BacktestReport(
            equity_curve=self._equity_curve,
            fills=self.broker.get_fills(),
            initial_balance=self.broker._initial_balance,
            symbol=self.symbol,
            interval=self._primary,
            start=str(self._equity_curve[0]["timestamp"]) if self._equity_curve else "",
            end=str(self._equity_curve[-1]["timestamp"]) if self._equity_curve else "",
            leverage=self.broker._leverage,
        )
        return report.generate()

    # ── 내부 ──────────────────────────────────────────────────────────────

    async def _listen(self, bsm: BinanceSocketManager, interval: str) -> None:
        """단일 시간봉 WebSocket 스트림 수신 루프."""
        async with bsm.futures_kline_socket(
            symbol=self.symbol,
            interval=interval,
        ) as stream:
            while self._running:
                msg = await stream.recv()
                candle = msg.get("k", {})

                # 미확정 캔들: primary 시간봉만 현재가 업데이트
                if not candle.get("x", False):
                    if interval == self._primary:
                        async with self._lock:
                            self.broker.update_price(self.symbol, float(candle["c"]))
                    continue

                await self._on_closed_candle(interval, candle)

    async def _on_closed_candle(self, interval: str, candle: dict) -> None:
        high  = float(candle["h"])
        low   = float(candle["l"])
        close = float(candle["c"])
        ts    = datetime.utcfromtimestamp(candle["T"] / 1000)

        market_data = MarketData(
            symbol=self.symbol,
            timestamp=ts,
            open=float(candle["o"]),
            high=high,
            low=low,
            close=close,
            volume=float(candle["v"]),
        )

        async with self._lock:
            self.broker.update_price(self.symbol, close)
            self.broker.check_limit_orders(self.symbol, high=high, low=low)

            signals = self.group.on_candle(interval, market_data)

            for signal in signals:
                broker_signal = BrokerSignal(
                    symbol=signal.symbol,
                    direction=OrderSide(signal.direction),
                    quantity=signal.quantity,
                    price=signal.price,
                    strategy_name=signal.strategy_name,
                    timestamp=signal.timestamp,
                )
                asyncio.create_task(self.broker.place_order(broker_signal))

            if interval == self._primary:
                self._equity_curve.append({
                    "timestamp": ts,
                    "equity": self.broker.get_total_equity(),
                    "price": close,
                })

        logger.info(
            "[%s][%s] close=%.2f equity=%.2f fills=%d",
            interval,
            ts.strftime("%Y-%m-%d %H:%M"),
            close,
            self.broker.get_total_equity(),
            len(self.broker.get_fills()),
        )
