import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.api.routers import portfolio, orders, strategy, ws
from backend.api.schemas import HealthResponse
from backend.api.state import app_state
from backend.broker.binance import BinanceBroker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작 시 브로커 연결
    if settings.BROKER == "binance" and settings.BINANCE_API_KEY:
        broker = BinanceBroker(
            api_key=settings.BINANCE_API_KEY,
            api_secret=settings.BINANCE_API_SECRET,
            testnet=settings.BINANCE_TESTNET,
        )
        await broker.connect()
        app_state.broker = broker
        logger.info("Broker connected: binance (testnet=%s)", settings.BINANCE_TESTNET)
    else:
        logger.warning("No broker configured — running with mock data")

    yield

    # 종료 시 브로커 연결 해제
    if app_state.broker:
        await app_state.broker.disconnect()
        logger.info("Broker disconnected")


app = FastAPI(
    title="QuantTrading API",
    version="0.1.0",
    description="Automated quantitative trading system API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(portfolio.router)
app.include_router(orders.router)
app.include_router(strategy.router)
app.include_router(ws.router)


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    """서버 및 브로커 연결 상태 확인."""
    return HealthResponse(
        status="ok",
        broker=settings.BROKER,
        broker_connected=app_state.broker is not None,
        timestamp=datetime.utcnow(),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.api.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=True,
    )
