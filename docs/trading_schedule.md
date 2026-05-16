# Trading Schedule Design

## Overview

The scheduler manages strategy activation windows, market session events, and periodic maintenance tasks.
It is implemented in `backend/core/scheduler.py` using APScheduler with `CronTrigger`.

All times are in **KST (Korea Standard Time, UTC+9)** unless stated otherwise.

---

## Market Sessions

### Korea Stock Exchange (KRX)

| Time (KST) | Event | Action |
|------------|-------|--------|
| 08:50 | `pre_market` | Data warm-up, indicator pre-computation |
| 09:00 | `market_open` | Activate strategies, connect live data stream |
| 09:00 ~ 09:30 | High volatility open | Scalping strategies: configurable ON/OFF |
| 09:30 ~ 11:30 | Morning session | All active strategies running |
| 11:30 ~ 13:00 | Lunch (low liquidity) | Scalping strategies paused by default |
| 13:00 ~ 15:20 | Afternoon session | All active strategies running |
| 15:20 | `pre_close` | Evaluate open positions for end-of-day closure |
| 15:30 | `market_close` | Deactivate strategies, generate daily report |
| 15:30 ~ 15:50 | `daily_report` | Calculate daily P&L, persist to DB |

### US Markets (NYSE / NASDAQ)

All times converted to KST:

| Time (KST) | Event | Description |
|------------|-------|-------------|
| 22:30 | `us_pre_market` | Data warm-up for US session |
| 23:30 | `us_market_open` | Activate US strategies |
| 06:00 (+1) | `us_market_close` | Deactivate US strategies, generate report |

### Cryptocurrency

- 24/7 operation — no market open/close events.
- Uses interval-based scheduling (e.g., every 1 minute, every 1 hour).
- Separate scheduler group: `crypto_scheduler`.

---

## Scheduler Events

```python
# Event names emitted by scheduler.py onto the event bus
EVENTS = [
    "pre_market",
    "market_open",
    "market_close",
    "pre_close",
    "daily_report",
    "us_pre_market",
    "us_market_open",
    "us_market_close",
    "rebalance",          # position strategies: weekly / monthly
    "crypto_interval",    # crypto strategies: configurable interval
]
```

Each event triggers corresponding lifecycle hooks on registered strategies:

| Scheduler Event | Strategy Hook |
|----------------|---------------|
| `market_open` | `on_start()` |
| `market_close` | `on_stop()` |
| `pre_close` | `on_pre_close()` (optional) |

---

## Strategy Activation by Timeframe

| Strategy Timeframe | Active Sessions |
|-------------------|----------------|
| Scalping | KRX morning (09:05~11:30), KRX afternoon (13:00~15:20) |
| Swing | KRX full session; signal generated at close, executed at next open |
| Position | Rebalance schedule only (weekly / monthly) |

---

## Rebalance Schedule

Position strategies follow a separate rebalance schedule:

| Interval | Cron Expression | Description |
|----------|----------------|-------------|
| Weekly | `0 9 * * 1` | Every Monday at 09:00 KST |
| Monthly | `0 9 1 * *` | First trading day of each month at 09:00 KST |

Rebalance flow:
1. `rebalance` event fires
2. Strategy `on_data()` is called with latest daily data
3. Strategy returns `Signal` list for position adjustments
4. OMS executes adjustments at market open price

---

## Abnormal Condition Handling

| Condition | Action |
|-----------|--------|
| Data stream disconnected mid-session | Reconnect with exponential backoff (max 5 retries) |
| Reconnect fails | Halt all strategies, log alert, notify via webhook |
| Portfolio drawdown exceeds threshold | `drawdown.py` halts all strategies immediately |
| Daily loss limit reached | Risk manager blocks all new orders for the day |
| Unexpected process crash | On restart, engine reconciles open positions with broker before resuming |

---

## APScheduler Configuration

```python
# backend/core/scheduler.py

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

# KRX sessions
scheduler.add_job(on_pre_market,    CronTrigger(hour=8,  minute=50, day_of_week="mon-fri"))
scheduler.add_job(on_market_open,   CronTrigger(hour=9,  minute=0,  day_of_week="mon-fri"))
scheduler.add_job(on_lunch_start,   CronTrigger(hour=11, minute=30, day_of_week="mon-fri"))
scheduler.add_job(on_lunch_end,     CronTrigger(hour=13, minute=0,  day_of_week="mon-fri"))
scheduler.add_job(on_pre_close,     CronTrigger(hour=15, minute=20, day_of_week="mon-fri"))
scheduler.add_job(on_market_close,  CronTrigger(hour=15, minute=30, day_of_week="mon-fri"))

# Rebalance
scheduler.add_job(on_rebalance_weekly,  CronTrigger(day_of_week="mon", hour=9, minute=0))
scheduler.add_job(on_rebalance_monthly, CronTrigger(day=1,             hour=9, minute=0))
```
