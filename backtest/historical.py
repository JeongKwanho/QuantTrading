import asyncio
import logging
from datetime import datetime

from binance import AsyncClient

from broker.mock import MockBroker
from strategies.base import MarketData
from strategies.groups import BaseGroup
from backend.broker.base import Signal as BrokerSignal, OrderSide

logger = logging.getLogger(__name__)


class HistoricalBacktest:
    """
    REST API로 과거 OHLCV 데이터를 가져와 전략 그룹을 검증한다.
    여러 시간봉을 병렬로 가져와 타임스탬프 순서로 정렬 후 그룹에 전달.
    레버리지/수수료는 MockBroker가 처리한다.
    """

    def __init__(
        self,
        group: BaseGroup,
        symbol: str,
        start: str,            # "2024-01-01"
        end: str,              # "2025-01-01"
        initial_balance: float = 10000.0,
        leverage: int = 1,
        slippage: float = 0.0,
        api_key: str = "",
        api_secret: str = "",
    ) -> None:
        self.group = group
        self.symbol = symbol
        self.start = start
        self.end = end
        self.broker = MockBroker(
            initial_balance=initial_balance,
            leverage=leverage,
            slippage=slippage,
        )
        self._api_key = api_key
        self._api_secret = api_secret

        # 그룹 내 모든 전략에 체결 콜백 연결
        def _on_fill(fill):
            for s in group.all_strategies():
                s.on_fill(fill)
        self.broker.set_on_fill(_on_fill)

    async def run(self) -> dict:
        """백테스트 실행. 완료 후 결과 dict 반환."""
        client = await AsyncClient.create(
            api_key=self._api_key,
            api_secret=self._api_secret,
        )

        try:
            # 모든 시간봉 데이터 병렬 수집
            timeframes = self.group.timeframes
            results = await asyncio.gather(
                *[self._fetch_all(client, tf) for tf in timeframes]
            )
            candles_by_tf: dict[str, list[dict]] = dict(zip(timeframes, results))

            total = sum(len(v) for v in candles_by_tf.values())
            logger.info(
                "Fetched %d candles total for %s %s",
                total, self.symbol, timeframes,
            )

            # 타임스탬프 순으로 병합 (같은 시각이면 큰봉 먼저)
            tf_order = {tf: i for i, tf in enumerate(timeframes)}
            merged: list[dict] = []
            for tf, candles in candles_by_tf.items():
                for c in candles:
                    merged.append({"timeframe": tf, **c})
            merged.sort(key=lambda x: (x["timestamp"], tf_order[x["timeframe"]]))

            self.broker.reset()
            self.group.reset()

            equity_curve: list[dict] = []
            primary = self.group.primary_timeframe

            for candle in merged:
                tf = candle["timeframe"]
                self.broker.update_price(self.symbol, candle["close"])
                self.broker.check_limit_orders(
                    self.symbol,
                    high=candle["high"],
                    low=candle["low"],
                )

                market_data = MarketData(
                    symbol=self.symbol,
                    timestamp=candle["timestamp"],
                    open=candle["open"],
                    high=candle["high"],
                    low=candle["low"],
                    close=candle["close"],
                    volume=candle["volume"],
                )
                signals = self.group.on_candle(tf, market_data)

                for signal in signals:
                    broker_signal = BrokerSignal(
                        symbol=signal.symbol,
                        direction=OrderSide(signal.direction),
                        quantity=signal.quantity,
                        price=signal.price,
                        strategy_name=signal.strategy_name,
                        timestamp=signal.timestamp,
                    )
                    await self.broker.place_order(broker_signal)

                # equity curve는 primary(가장 작은) 시간봉 기준
                if tf == primary:
                    equity_curve.append({
                        "timestamp": candle["timestamp"],
                        "equity": self.broker.get_total_equity(),
                        "price": candle["close"],
                    })

        finally:
            await client.close_connection()

        from backtest.report import BacktestReport
        report = BacktestReport(
            equity_curve=equity_curve,
            fills=self.broker.get_fills(),
            initial_balance=self.broker._initial_balance,
            symbol=self.symbol,
            interval=primary,
            start=self.start,
            end=self.end,
            leverage=self.broker._leverage,
        )
        return report.generate()

    async def _fetch_all(self, client: AsyncClient, interval: str) -> list[dict]:
        """start ~ end 기간의 모든 캔들을 페이징해서 가져온다."""
        candles = []
        start_ts = int(datetime.strptime(self.start, "%Y-%m-%d").timestamp() * 1000)
        end_ts   = int(datetime.strptime(self.end,   "%Y-%m-%d").timestamp() * 1000)

        while start_ts < end_ts:
            raw = await client.futures_klines(
                symbol=self.symbol,
                interval=interval,
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
            start_ts = raw[-1][0] + 1

        return candles
