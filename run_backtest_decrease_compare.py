"""
Compare TrendChannelV1 with DecreaseTrendChannerV1.

Run:
    python run_backtest_decrease_compare.py
"""

import asyncio
import sys
from datetime import datetime
from typing import Type

from binance import AsyncClient

from backend.broker.base import OrderSide, Signal as BrokerSignal
from broker.mock import MockBroker
from strategies.base import BaseStrategy, FillEvent, MarketData
from strategies.decrease_trend_channer_v1 import DecreaseTrendChannerV1
from strategies.trend_channel_v1 import TrendChannelV1

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")


SYMBOL = "BTCUSDT"
N_CANDLES = 10_000
INITIAL_BALANCE = 10_000.0
LEVERAGE = 1
RISK_PCT = 0.95
WINDOW = 50
PIVOT_K = 2
MIN_RR = 2.0
COOLDOWN = 5

SCENARIOS = [
    {"label": "Position", "timeframe": "1d"},
    {"label": "Swing", "timeframe": "4h"},
    {"label": "Scalping", "timeframe": "1h"},
    {"label": "Scalp15m", "timeframe": "15m"},
    {"label": "Scalp5m", "timeframe": "5m"},
]

INTERVAL_SECONDS = {
    "1d": 86400,
    "4h": 14400,
    "1h": 3600,
    "15m": 900,
    "5m": 300,
}


async def fetch_candles(client: AsyncClient, symbol: str, interval: str, n: int) -> list[dict]:
    now_ts = int(datetime.utcnow().timestamp() * 1000)
    start_ts = now_ts - n * INTERVAL_SECONDS[interval] * 1000
    candles: list[dict] = []

    while start_ts < now_ts and len(candles) < n:
        raw = await client.futures_klines(
            symbol=symbol,
            interval=interval,
            startTime=start_ts,
            endTime=now_ts,
            limit=1500,
        )
        if not raw:
            break
        for kline in raw:
            candles.append({
                "timestamp": datetime.utcfromtimestamp(kline[0] / 1000),
                "open": float(kline[1]),
                "high": float(kline[2]),
                "low": float(kline[3]),
                "close": float(kline[4]),
                "volume": float(kline[5]),
            })
        start_ts = raw[-1][0] + 1

    return candles[:n]


async def run_single(
    candles: list[dict],
    symbol: str,
    strategy_cls: Type[BaseStrategy],
    strategy_kwargs: dict | None = None,
    bull_flags: list[bool] | None = None,
    external_entry_flags: list[bool] | None = None,
    zero_fee: bool = False,
) -> dict:
    kwargs = strategy_kwargs or {}
    strategy = strategy_cls(
        leverage=LEVERAGE,
        window=WINDOW,
        pivot_k=PIVOT_K,
        min_rr=MIN_RR,
        cooldown=COOLDOWN,
        **kwargs,
    )
    broker = MockBroker(
        initial_balance=INITIAL_BALANCE,
        leverage=LEVERAGE,
        **({} if not zero_fee else {"taker_fee": 0.0, "maker_fee": 0.0}),
    )

    def on_fill(fill) -> None:
        strategy.on_fill(FillEvent(
            order_id=fill.order_id,
            symbol=fill.symbol,
            direction=fill.side.value,
            price=fill.price,
            quantity=fill.quantity,
            fee=fill.fee,
            timestamp=fill.timestamp,
        ))

    broker.set_on_fill(on_fill)

    trade_log: list[dict] = []
    equity_curve: list[float] = []

    for idx, candle in enumerate(candles):
        broker.update_price(symbol, candle["close"])
        indicators = {}
        if bull_flags is not None:
            indicators["bull_market"] = bull_flags[idx]
        data = MarketData(
            symbol=symbol,
            timestamp=candle["timestamp"],
            open=candle["open"],
            high=candle["high"],
            low=candle["low"],
            close=candle["close"],
            volume=candle["volume"],
            indicators=indicators,
        )

        for sig in strategy.on_data(data):
            if (
                external_entry_flags is not None
                and sig.direction == "BUY"
                and not external_entry_flags[idx]
            ):
                if hasattr(strategy, "_reset_position"):
                    strategy._reset_position()
                continue

            quantity = sig.quantity
            if sig.direction == "BUY" and quantity == 0.0:
                quantity = (broker._balance * RISK_PCT) / candle["close"]
            if quantity <= 0.0:
                continue

            reason = sig.metadata.get("reason", "")
            override_price = None
            if reason == "stop_loss":
                override_price = sig.metadata.get("sl_price")
            elif reason == "tp1_upper":
                override_price = sig.metadata.get("tp1_price")
            elif reason == "tp2_h2":
                override_price = sig.metadata.get("h2_price")

            if override_price is not None:
                broker._current_prices[sig.symbol] = override_price

            order = await broker.place_order(BrokerSignal(
                symbol=sig.symbol,
                direction=OrderSide(sig.direction),
                quantity=quantity,
                price=None,
                strategy_name=sig.strategy_name,
                timestamp=sig.timestamp,
            ))

            if override_price is not None:
                broker._current_prices[sig.symbol] = candle["close"]

            fill_price = order.avg_fill_price if order.avg_fill_price is not None else candle["close"]
            trade_log.append({
                "date": candle["timestamp"].strftime("%Y-%m-%d %H:%M"),
                "dir": sig.direction,
                "qty": quantity,
                "price": fill_price,
                "reason": reason,
                "equity": broker.get_total_equity(),
            })

        equity_curve.append(broker.get_total_equity())

    fills = broker.get_fills()
    buy_fills = [fill for fill in fills if fill.side == OrderSide.BUY]
    sell_reasons = [trade["reason"] for trade in trade_log if trade["dir"] == "SELL"]
    final = broker.get_total_equity()
    ret = (final - INITIAL_BALANCE) / INITIAL_BALANCE * 100
    total_fee = sum(fill.fee for fill in fills)

    peak = INITIAL_BALANCE
    mdd = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak * 100
        mdd = max(mdd, drawdown)

    return {
        "final": final,
        "return_pct": ret,
        "mdd": mdd,
        "fee": total_fee,
        "entries": len(buy_fills),
        "sl": sell_reasons.count("stop_loss"),
        "tp1": sell_reasons.count("tp1_upper"),
        "tp2": sell_reasons.count("tp2_h2"),
        "trade_log": trade_log,
    }


def build_bull_flags(
    candles: list[dict],
    higher_candles: list[dict],
    fast_period: int = 5,
    slow_period: int = 20,
    grace_bars: int = 50,
) -> list[bool]:
    higher_states: list[tuple[datetime, bool, int]] = []
    closes: list[float] = []

    for state_idx, candle in enumerate(higher_candles):
        closes.append(candle["close"])
        if len(closes) >= slow_period:
            fast = sum(closes[-fast_period:]) / fast_period
            slow = sum(closes[-slow_period:]) / slow_period
            bull = candle["close"] > slow and fast > slow
        else:
            bull = False
        higher_states.append((candle["timestamp"], bull, state_idx))

    flags: list[bool] = []
    h_idx = 0
    current_bull = False
    current_state_idx = -1
    last_bull_state_idx: int | None = None
    for candle in candles:
        while h_idx < len(higher_states) and higher_states[h_idx][0] <= candle["timestamp"]:
            current_bull = higher_states[h_idx][1]
            current_state_idx = higher_states[h_idx][2]
            if current_bull:
                last_bull_state_idx = current_state_idx
            h_idx += 1
        recently_bull = (
            last_bull_state_idx is not None
            and current_state_idx - last_bull_state_idx <= grace_bars
        )
        flags.append(current_bull or recently_bull)

    return flags


async def main() -> None:
    print("Connecting Binance and fetching candles ...\n")
    client = await AsyncClient.create()

    candles_by_label: dict[str, tuple[str, list[dict]]] = {}
    try:
        for scenario in SCENARIOS:
            label = scenario["label"]
            timeframe = scenario["timeframe"]
            print(f"  [{label}] fetching {N_CANDLES} x {timeframe} candles ...", end=" ", flush=True)
            candles = await fetch_candles(client, SYMBOL, timeframe, N_CANDLES)
            print(f"{len(candles)} candles ({candles[0]['timestamp'].date()} ~ {candles[-1]['timestamp'].date()})")
            candles_by_label[label] = (timeframe, candles)
    finally:
        await client.close_connection()

    runs: dict[str, dict[str, tuple[str, dict]]] = {
        "base": {},
        "base_bull": {},
        "improved": {},
        "bull": {},
    }

    for label, (timeframe, candles) in candles_by_label.items():
        print(f"\nRunning {label} ({timeframe}) ...")
        base = await run_single(candles, SYMBOL, TrendChannelV1)
        bull_flags = build_bull_flags(candles, candles)
        base_bull = await run_single(
            candles,
            SYMBOL,
            TrendChannelV1,
            external_entry_flags=bull_flags,
        )
        improved = await run_single(candles, SYMBOL, DecreaseTrendChannerV1)
        bull = await run_single(
            candles,
            SYMBOL,
            DecreaseTrendChannerV1,
            strategy_kwargs={"require_bull_market": True},
            bull_flags=bull_flags,
        )
        runs["base"][label] = (timeframe, base)
        runs["base_bull"][label] = (timeframe, base_bull)
        runs["improved"][label] = (timeframe, improved)
        runs["bull"][label] = (timeframe, bull)
        print(f"  base entries={base['entries']} return={base['return_pct']:+.2f}%")
        print(f"  base+bull entries={base_bull['entries']} return={base_bull['return_pct']:+.2f}%")
        print(f"  improved entries={improved['entries']} return={improved['return_pct']:+.2f}%")
        print(f"  bull-filter entries={bull['entries']} return={bull['return_pct']:+.2f}%")

    width = 126
    print()
    print("=" * width)
    print(f"  {SYMBOL} TrendChannelV1 vs DecreaseTrendChannerV1 | candles={N_CANDLES} window={WINDOW} pivot_k={PIVOT_K} min_rr={MIN_RR}")
    print("=" * width)
    print(
        f"  {'Type':<10} {'TF':<5} "
        f"{'Base':>9} {'Base+B':>9} {'New':>9} {'New+B':>9} "
        f"{'BaseEnt':>8} {'BaseBEnt':>8} {'NewEnt':>8} {'NewBEnt':>8} "
        f"{'BaseSL':>7} {'BaseBSL':>7} {'NewSL':>7} {'NewBSL':>7}"
    )
    print("-" * width)

    for label, (timeframe, base) in runs["base"].items():
        improved = runs["improved"][label][1]
        base_bull = runs["base_bull"][label][1]
        bull = runs["bull"][label][1]
        print(
            f"  {label:<10} {timeframe:<5} "
            f"{base['return_pct']:>+8.2f}% {base_bull['return_pct']:>+8.2f}% "
            f"{improved['return_pct']:>+8.2f}% {bull['return_pct']:>+8.2f}% "
            f"{base['entries']:>8} {base_bull['entries']:>8} {improved['entries']:>8} {bull['entries']:>8} "
            f"{base['sl']:>7} {base_bull['sl']:>7} {improved['sl']:>7} {bull['sl']:>7}"
        )

    print("=" * width)


if __name__ == "__main__":
    asyncio.run(main())
