import asyncio
import logging
from datetime import datetime

from binance import AsyncClient, BinanceSocketManager

from broker.mock import MockBroker
from strategies.base import BaseStrategy, MarketData
from backend.broker.base import Signal as BrokerSignal, OrderSide

logger = logging.getLogger(__name__)


class LivePaperBacktest:
    """
    실제 Binance WebSocket에서 실시간 캔들을 받아 전략을 검증한다.
    주문은 MockBroker로 처리 — 실제 돈이 나가지 않는다.
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        symbol: str,
        interval: str,
        initial_balance: float = 10000.0,
        leverage: int = 1,
        slippage: float = 0.0,
        api_key: str = "",
        api_secret: str = "",
    ) -> None:
        self.strategy = strategy
        self.symbol = symbol
        self.interval = interval
        self.broker = MockBroker(
            initial_balance=initial_balance,
            leverage=leverage,
            slippage=slippage,
        )
        self._api_key = api_key
        self._api_secret = api_secret
        self._running = False
        self._equity_curve: list[dict] = []

        self.broker.set_on_fill(self.strategy.on_fill)

    async def start(self) -> None:
        """실시간 페이퍼 트레이딩 시작. Ctrl+C로 중단."""
        client = await AsyncClient.create(
            api_key=self._api_key,
            api_secret=self._api_secret,
        )
        bsm = BinanceSocketManager(client)
        self._running = True

        logger.info(
            "Live paper backtest started — %s %s (leverage=%dx)",
            self.symbol, self.interval, self.broker._leverage,
        )
        self.strategy.on_start()

        try:
            async with bsm.futures_kline_socket(
                symbol=self.symbol,
                interval=self.interval,
            ) as stream:
                while self._running:
                    msg = await stream.recv()
                    candle = msg.get("k", {})

                    # 확정된 캔들(closed)만 전략에 전달
                    if not candle.get("x", False):
                        # 미확정 캔들: 현재가만 업데이트
                        self.broker.update_price(self.symbol, float(candle["c"]))
                        continue

                    self._on_closed_candle(candle)

        except asyncio.CancelledError:
            pass
        finally:
            self.strategy.on_stop()
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
            interval=self.interval,
            start=str(self._equity_curve[0]["timestamp"]) if self._equity_curve else "",
            end=str(self._equity_curve[-1]["timestamp"]) if self._equity_curve else "",
            leverage=self.broker._leverage,
        )
        return report.generate()

    # ── 내부 ──────────────────────────────────────────────────────────────

    def _on_closed_candle(self, candle: dict) -> None:
        high  = float(candle["h"])
        low   = float(candle["l"])
        close = float(candle["c"])
        ts    = datetime.utcfromtimestamp(candle["T"] / 1000)

        self.broker.update_price(self.symbol, close)
        self.broker.check_limit_orders(self.symbol, high=high, low=low)

        market_data = MarketData(
            symbol=self.symbol,
            timestamp=ts,
            open=float(candle["o"]),
            high=high,
            low=low,
            close=close,
            volume=float(candle["v"]),
        )
        signals = self.strategy.on_data(market_data)

        for signal in signals:
            signal.strategy_name = self.strategy.name
            broker_signal = BrokerSignal(
                symbol=signal.symbol,
                direction=OrderSide(signal.direction),
                quantity=signal.quantity,
                price=signal.price,
                strategy_name=signal.strategy_name,
                timestamp=signal.timestamp,
            )
            asyncio.create_task(self.broker.place_order(broker_signal))

        self._equity_curve.append({
            "timestamp": ts,
            "equity": self.broker.get_total_equity(),
            "price": close,
        })

        logger.info(
            "[%s] close=%.2f equity=%.2f fills=%d",
            ts.strftime("%Y-%m-%d %H:%M"),
            close,
            self.broker.get_total_equity(),
            len(self.broker.get_fills()),
        )
