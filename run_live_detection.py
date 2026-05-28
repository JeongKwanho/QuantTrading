"""
Live combined-detection monitor.

Watches selected Binance futures symbols/timeframes, stores closed candles and
detection snapshots in SQLite, and records an event when the rolling 15-candle
combined detection changes from 0 to 1.

Run:
    python run_live_detection.py
"""

import asyncio
import json
import logging
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from binance import AsyncClient, BinanceSocketManager

from detection.combined import CombinedDetectionJudge, DetectionSnapshot
from strategies.base import MarketData


if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")


SYMBOLS = [
    "XRPUSDT",
    "BTCUSDT",
    "ETHUSDT",
    "HBARUSDT",
    "RIFUSDT",
    "WLDUSDC",
]
INTERVALS = ["5m", "15m", "1h", "4h", "1d"]
DB_PATH = Path("data/live_detection.sqlite3")
LOG_PATH = Path("logs/live_detection.log")
WARMUP_CANDLES = 250
LOOKBACK_BARS = 15
RECONNECT_DELAY_SECONDS = 5
MAX_RECONNECT_DELAY_SECONDS = 60

MIN_GAP_PCT_BY_TF = {
    "5m": 0.0010,
    "15m": 0.0012,
    "1h": 0.0015,
    "4h": 0.0020,
    "1d": 0.0030,
}

logger = logging.getLogger("live_detection")


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console_handler)


def log_info(message: str) -> None:
    logger.info(message)


def log_error(message: str, exc: Exception | None = None) -> None:
    if exc is None:
        logger.error(message)
    else:
        logger.exception(message)


class DetectionDatabase:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()

    def close(self) -> None:
        self.conn.close()

    def _create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS candles (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (symbol, interval, close_time)
            );

            CREATE TABLE IF NOT EXISTS detection_snapshots (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                close_time TEXT NOT NULL,
                lookback_bars INTEGER NOT NULL,
                bar_index INTEGER NOT NULL,
                detected_count INTEGER NOT NULL,
                all_three_detected INTEGER NOT NULL,
                detected_names TEXT NOT NULL,
                trend_detected INTEGER NOT NULL,
                trend_current INTEGER NOT NULL,
                trend_bars_since INTEGER,
                trend_direction TEXT,
                ob_detected INTEGER NOT NULL,
                ob_current INTEGER NOT NULL,
                ob_bars_since INTEGER,
                ob_direction TEXT,
                fvg_detected INTEGER NOT NULL,
                fvg_current INTEGER NOT NULL,
                fvg_bars_since INTEGER,
                fvg_direction TEXT,
                details_json TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (symbol, interval, close_time)
            );

            CREATE TABLE IF NOT EXISTS detection_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                close_time TEXT NOT NULL,
                detected_count INTEGER NOT NULL,
                detected_names TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_time
                ON detection_events (close_time, symbol, interval);
            CREATE INDEX IF NOT EXISTS idx_snapshots_state
                ON detection_snapshots (all_three_detected, close_time);
            """
        )
        self.conn.commit()

    def save_candle(
        self,
        symbol: str,
        interval: str,
        open_time: datetime,
        close_time: datetime,
        data: MarketData,
        source: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO candles (
                symbol, interval, open_time, close_time,
                open, high, low, close, volume, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                interval,
                open_time.isoformat(sep=" "),
                close_time.isoformat(sep=" "),
                data.open,
                data.high,
                data.low,
                data.close,
                data.volume,
                source,
                datetime.utcnow().isoformat(sep=" "),
            ),
        )

    def save_snapshot(
        self,
        symbol: str,
        interval: str,
        close_time: datetime,
        snapshot: DetectionSnapshot,
        source: str,
    ) -> None:
        trend = snapshot.items["trend_line"]
        ob = snapshot.items["order_block"]
        fvg = snapshot.items["fair_value_gap"]
        self.conn.execute(
            """
            INSERT OR REPLACE INTO detection_snapshots (
                symbol, interval, close_time, lookback_bars, bar_index,
                detected_count, all_three_detected, detected_names,
                trend_detected, trend_current, trend_bars_since, trend_direction,
                ob_detected, ob_current, ob_bars_since, ob_direction,
                fvg_detected, fvg_current, fvg_bars_since, fvg_direction,
                details_json, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                interval,
                close_time.isoformat(sep=" "),
                snapshot.lookback_bars,
                snapshot.bar_index,
                snapshot.detected_count,
                int(snapshot.all_three_detected),
                ",".join(snapshot.detected_names()),
                int(trend.detected),
                int(trend.currently_detected),
                trend.bars_since_detected,
                trend.direction,
                int(ob.detected),
                int(ob.currently_detected),
                ob.bars_since_detected,
                ob.direction,
                int(fvg.detected),
                int(fvg.currently_detected),
                fvg.bars_since_detected,
                fvg.direction,
                _json_dumps(asdict(snapshot)),
                source,
                datetime.utcnow().isoformat(sep=" "),
            ),
        )

    def save_event(
        self,
        symbol: str,
        interval: str,
        close_time: datetime,
        snapshot: DetectionSnapshot,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO detection_events (
                symbol, interval, close_time, detected_count,
                detected_names, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                interval,
                close_time.isoformat(sep=" "),
                snapshot.detected_count,
                ",".join(snapshot.detected_names()),
                _json_dumps(asdict(snapshot)),
                datetime.utcnow().isoformat(sep=" "),
            ),
        )

    def commit(self) -> None:
        self.conn.commit()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def _make_judge(interval: str) -> CombinedDetectionJudge:
    return CombinedDetectionJudge(
        lookback_bars=LOOKBACK_BARS,
        fvg_min_gap_pct=MIN_GAP_PCT_BY_TF[interval],
    )


def _market_data_from_rest(symbol: str, kline: list) -> tuple[datetime, datetime, MarketData]:
    open_time = datetime.utcfromtimestamp(kline[0] / 1000)
    close_time = datetime.utcfromtimestamp(kline[6] / 1000)
    data = MarketData(
        symbol=symbol,
        timestamp=close_time,
        open=float(kline[1]),
        high=float(kline[2]),
        low=float(kline[3]),
        close=float(kline[4]),
        volume=float(kline[5]),
    )
    return open_time, close_time, data


def _market_data_from_ws(symbol: str, candle: dict) -> tuple[datetime, datetime, MarketData]:
    open_time = datetime.utcfromtimestamp(candle["t"] / 1000)
    close_time = datetime.utcfromtimestamp(candle["T"] / 1000)
    data = MarketData(
        symbol=symbol,
        timestamp=close_time,
        open=float(candle["o"]),
        high=float(candle["h"]),
        low=float(candle["l"]),
        close=float(candle["c"]),
        volume=float(candle["v"]),
    )
    return open_time, close_time, data


def _format_item_age(snapshot: DetectionSnapshot) -> str:
    parts = []
    for name in ("trend_line", "order_block", "fair_value_gap"):
        item = snapshot.items[name]
        age = "-" if item.bars_since_detected is None else str(item.bars_since_detected)
        current = "*" if item.currently_detected else ""
        parts.append(f"{name}={age}{current}")
    return " ".join(parts)


async def warmup(
    client: AsyncClient,
    db: DetectionDatabase,
    judges: dict[tuple[str, str], CombinedDetectionJudge],
    last_state: dict[tuple[str, str], bool],
) -> None:
    now_ms = int(datetime.utcnow().timestamp() * 1000)
    for symbol in SYMBOLS:
        for interval in INTERVALS:
            key = (symbol, interval)
            raw = await client.futures_klines(
                symbol=symbol,
                interval=interval,
                limit=WARMUP_CANDLES,
            )
            closed = [kline for kline in raw if int(kline[6]) <= now_ms]
            for kline in closed:
                open_time, close_time, data = _market_data_from_rest(symbol, kline)
                snapshot = judges[key].evaluate(data)
                db.save_candle(symbol, interval, open_time, close_time, data, "warmup")
                db.save_snapshot(symbol, interval, close_time, snapshot, "warmup")

            last_snapshot = snapshot if closed else None
            last_state[key] = bool(last_snapshot and last_snapshot.all_three_detected)
            db.commit()
            state = "1" if last_state[key] else "0"
            log_info(f"[warmup] {symbol:<8} {interval:<3} candles={len(closed):>3} state={state}")


async def run_stream_once(db: DetectionDatabase) -> None:
    judges = {
        (symbol, interval): _make_judge(interval)
        for symbol in SYMBOLS
        for interval in INTERVALS
    }
    last_state = {(symbol, interval): False for symbol in SYMBOLS for interval in INTERVALS}

    client = await AsyncClient.create()
    bsm = BinanceSocketManager(client)
    streams = [
        f"{symbol.lower()}@kline_{interval}"
        for symbol in SYMBOLS
        for interval in INTERVALS
    ]

    try:
        await warmup(client, db, judges, last_state)
        log_info("Live detection stream connected.")

        async with bsm.futures_multiplex_socket(streams) as stream:
            while True:
                msg = await stream.recv()
                payload = msg.get("data", msg)
                candle = payload.get("k", {})
                if not candle or not candle.get("x", False):
                    continue

                symbol = candle["s"]
                interval = candle["i"]
                key = (symbol, interval)
                if key not in judges:
                    continue

                open_time, close_time, data = _market_data_from_ws(symbol, candle)
                snapshot = judges[key].evaluate(data)
                was_one = last_state[key]
                is_one = snapshot.all_three_detected

                db.save_candle(symbol, interval, open_time, close_time, data, "live")
                db.save_snapshot(symbol, interval, close_time, snapshot, "live")
                if is_one and not was_one:
                    db.save_event(symbol, interval, close_time, snapshot)
                    log_info(
                        f"[DETECTED=1] {close_time:%Y-%m-%d %H:%M:%S} "
                        f"{symbol:<8} {interval:<3} close={data.close:.8g} "
                        f"{_format_item_age(snapshot)}"
                    )
                else:
                    log_info(
                        f"[live] {close_time:%Y-%m-%d %H:%M:%S} "
                        f"{symbol:<8} {interval:<3} state={int(is_one)} "
                        f"count={snapshot.detected_count} close={data.close:.8g} "
                        f"{_format_item_age(snapshot)}"
                    )

                last_state[key] = is_one
                db.commit()
    finally:
        await client.close_connection()


async def monitor_forever() -> None:
    db = DetectionDatabase(DB_PATH)
    streams_count = len(SYMBOLS) * len(INTERVALS)
    reconnect_delay = RECONNECT_DELAY_SECONDS

    log_info(f"DB: {DB_PATH.resolve()}")
    log_info(f"Log: {LOG_PATH.resolve()}")
    log_info(f"Watching {len(SYMBOLS)} symbols x {len(INTERVALS)} intervals = {streams_count} streams")
    log_info("Live detection started. Press Ctrl+C to stop.")

    try:
        while True:
            try:
                await run_stream_once(db)
                reconnect_delay = RECONNECT_DELAY_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                log_error(
                    f"[reconnect] {now} stream error: {type(exc).__name__}: {exc}. "
                    f"Retrying in {reconnect_delay}s...",
                    exc,
                )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY_SECONDS)
    finally:
        db.close()


if __name__ == "__main__":
    setup_logging()
    try:
        asyncio.run(monitor_forever())
    except KeyboardInterrupt:
        log_info("Live detection stopped.")
