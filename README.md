# QuantTrading

Automated quantitative trading system built in Python.

## Overview

This project implements a fully automated trading system with a clear separation between backend logic and frontend dashboard. It supports multiple trading strategies across different time horizons (scalping, swing, and position trading).

## Project Structure

```
QuantTrading/
├── backend/        # Core engine, data, broker adapters, OMS, risk, API server
├── frontend/       # Real-time dashboard and strategy control UI
├── strategies/     # Trading strategy modules
├── backtest/       # Backtesting engine
├── docs/           # Design documents
└── config/         # Configuration templates
```

## Documentation

- [Backend Design](docs/backend.md)

## Setup

1. Clone the repository
2. Copy `.env.example` to `.env` and fill in your credentials
3. Install dependencies: `pip install -r requirements.txt`
4. Run the backend: `python backend/api/main.py`

## Security

API keys and secrets are stored in `.env` (local only, never committed).  
See `.env.example` for the required environment variable names.

## License

Private repository — all rights reserved.
