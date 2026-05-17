import asyncio
import logging
from datetime import datetime

from binance import AsyncClient

from broker.mock import MockBroker
from strategies.base import BaseStrategy, MarketData

logger = logging.getLogger(__name__)


class HistoricalBacktest:
    """
    REST API로 과거 OHLCV 데이터를 가져와 전략을 검증한다.
    레버리지/수수료는 MockBroker가 처리한다.
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        symbol: str,
        interval: str,
        start: str,            # "2024-01-01"
        end: str,              # "2025-01-01"
        initial_balance: float = 10000.0,
        leverage: int = 1,
        slippage: float = 0.0,
        api_key: str = "",
        api_secret: str = "",
    ) -> None:
        self.strategy = strategy
        self.symbol = symbol
        self.interval = interval
        self.start = start
        self.end = end
        self.broker = MockBroker(
            initial_balance=initial_balance,
            leverage=leverage,
            slippage=slippage,
        )
        self._api_key = api_key
        self._api_secret = api_secret

        # 전략에 체결 콜백 연결
        self.broker.set_on_fill(self.strategy.on_fill)

    async def run(self) -> dict:
        """백테스트 실행. 완료 후 결과 dict 반환."""
        client = await AsyncClient.create(
            api_key=self._api_key,
            api_secret=self._api_secret,
        )

        try:
            candles = await self._fetch_all(client)
            logger.info("Fetched %d candles for %s %s", len(candles), self.symbol, self.interval)

            self.broker.reset()
            self.strategy.on_start()

            equity_curve: list[dict] = []

            for candle in candles:
                # 현재가를 MockBroker에 알려줌 (청산 체크 + 지정가 체결)
                self.broker.update_price(self.symbol, candle["close"])
                self.broker.check_limit_orders(
                    self.symbol,
                    high=candle["high"],
                    low=candle["low"],
                )

                # 전략 호출
                market_data = MarketData(
                    symbol=self.symbol,
                    timestamp=candle["timestamp"],
                    open=candle["open"],
                    high=candle["high"],
                    low=candle["low"],
                    close=candle["close"],
                    volume=candle["volume"],
                )
                signals = self.strategy.on_data(market_data)

                # Signal → MockBroker 주문
                for signal in signals:
                    signal.strategy_name = self.strategy.name
                    from backend.broker.base import Signal as BrokerSignal, OrderSide
                    broker_signal = BrokerSignal(
                        symbol=signal.symbol,
                        direction=OrderSide(signal.direction),
                        quantity=signal.quantity,
                        price=signal.price,
                        strategy_name=signal.strategy_name,
                        timestamp=signal.timestamp,
                    )
                    await self.broker.place_order(broker_signal)

                equity_curve.append({
                    "timestamp": candle["timestamp"],
                    "equity": self.broker.get_total_equity(),
                    "price": candle["close"],
                })

            self.strategy.on_stop()

        finally:
            await client.close_connection()

        from backtest.report import BacktestReport
        report = BacktestReport(
            equity_curve=equity_curve,
            fills=self.broker.get_fills(),
            initial_balance=self.broker._initial_balance,
            symbol=self.symbol,
            interval=self.interval,
            start=self.start,
            end=self.end,
            leverage=self.broker._leverage,
        )
        return report.generate()

    async def _fetch_all(self, client: AsyncClient) -> list[dict]:
        """start ~ end 기간의 모든 캔들을 페이징해서 가져온다."""
        candles = []
        start_ts = int(datetime.strptime(self.start, "%Y-%m-%d").timestamp() * 1000)
        end_ts   = int(datetime.strptime(self.end,   "%Y-%m-%d").timestamp() * 1000)

        while start_ts < end_ts:
            raw = await client.futures_klines(
                symbol=self.symbol,
                interval=self.interval,
                startTime=start_ts,
                endTime=end_ts,
                limit=1500,
            )
            if not raw:
                break
            for k in raw:
                candles.append({
                    "timestamp": datetime.utcfromtimestamp(k[0] / 1000),
                    "open":   float(k[1]),
                    "high":   float(k[2]),
                    "low":    float(k[3]),
                    "close":  float(k[4]),
                    "volume": float(k[5]),
                })
            start_ts = raw[-1][0] + 1  # 다음 페이지

        return candles
