import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.analytics import AnalyticsService
from backend.backtest import BacktestService
from backend.config import settings
from backend.database import CandleRecord, Trade, db_session, init_db
from backend.exchange import ExchangeService
from backend.journal import JournalService
from backend.orders import OrderService
from backend.risk import RiskConfig, RiskManager
from backend.scheduler import SchedulerService
from backend.screenshots import ScreenshotService
from backend.strategy import StrategyEngine
from backend.telegram import TelegramService
from backend.websocket import CandleStream

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("delta-bot")

exchange = ExchangeService()
candles = CandleStream()
orders = OrderService(exchange)
scheduler = SchedulerService()
strategy_engine = StrategyEngine(rr=settings.default_rr, reentry_buffer_pct=settings.reentry_buffer_pct)
risk_manager = RiskManager(
    RiskConfig(
        risk_per_trade_pct=settings.risk_per_trade_pct,
        daily_loss_limit_pct=settings.daily_loss_limit_pct,
        max_trades_per_day=settings.max_trades_per_day,
        max_leverage=settings.max_leverage,
    )
)
analytics = AnalyticsService()
journal = JournalService()
screenshots = ScreenshotService()
backtest_service = BacktestService()
telegram = TelegramService()

runtime_state: dict[str, Any] = {
    "equity": settings.default_equity,
    "leverage": settings.default_leverage,
    "day_stats": {"trades": {}, "loss": {}},
    "open_trades": {},
    "status_by_symbol": {},
}


class LoginRequest(BaseModel):
    api_key: str = ""
    api_secret: str = ""


class PlaceOrderRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=20)
    side: str = Field(pattern="^(buy|sell|BUY|SELL)$")
    quantity: float = Field(gt=0)
    order_type: str = Field(default="market", pattern="^(market|limit)$")
    price: float | None = Field(default=None, gt=0)


class StrategyToggleRequest(BaseModel):
    enabled: bool


class BacktestRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=20)
    rr: float = Field(default=2.0, gt=0)
    reentry_buffer_pct: float = Field(default=0.10, ge=0)
    lookback: int = Field(default=1000, gt=100, le=5000)


class OptimizeRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=20)
    rr_values: list[float] = Field(default_factory=lambda: [1.5, 2.0, 2.5, 3.0])
    reentry_buffer_pct: float = Field(default=0.10, ge=0)
    lookback: int = Field(default=1500, gt=100, le=5000)


async def process_candle(candle: dict[str, Any]) -> None:
    symbol = candle["symbol"].upper()

    with db_session() as session:
        session.add(
            CandleRecord(
                symbol=symbol,
                timeframe=candle["timeframe"],
                ts=datetime.fromisoformat(candle["ts"].replace("Z", "+00:00")).replace(tzinfo=None),
                open=float(candle["open"]),
                high=float(candle["high"]),
                low=float(candle["low"]),
                close=float(candle["close"]),
                volume=float(candle["volume"]),
            )
        )

    await _check_open_trade_exit(symbol, candle)

    signal, status = strategy_engine.on_candle(candle)
    runtime_state["status_by_symbol"][symbol] = status

    if signal is None:
        return

    if symbol in runtime_state["open_trades"]:
        return

    decision = risk_manager.evaluate_entry(
        equity=float(runtime_state["equity"]),
        day_stats=runtime_state["day_stats"],
        entry_price=signal.entry,
        stop_price=signal.stop_loss,
        leverage=int(runtime_state["leverage"]),
        now=datetime.now(UTC),
    )

    if not decision.approved:
        journal.log_event(
            "risk_blocked",
            {
                "reason": decision.reason,
                "symbol": symbol,
                "strategy": signal.strategy,
            },
            level="WARN",
            symbol=symbol,
            strategy=signal.strategy,
        )
        await telegram.send(
            f"⚠️ Risk blocked {symbol}: {decision.reason}"
        )
        return

    if exchange.is_paper() is False and settings.live_trading_enabled is False:
        journal.log_event(
            "live_trade_blocked",
            {
                "reason": "LIVE_TRADING_ENABLED is false",
                "symbol": symbol,
                "strategy": signal.strategy,
            },
            level="WARN",
            symbol=symbol,
            strategy=signal.strategy,
        )
        await telegram.send(f"⚠️ Live trade blocked {symbol}: LIVE_TRADING_ENABLED is false")
        return

    await orders.place(
        {
            "symbol": signal.symbol,
            "side": signal.side,
            "quantity": decision.quantity,
            "order_type": "market",
            "price": signal.entry,
        }
    )

    with db_session() as session:
        trade = Trade(
            strategy=signal.strategy,
            symbol=signal.symbol,
            side=signal.side,
            quantity=decision.quantity,
            leverage=runtime_state["leverage"],
            risk_amount=decision.risk_amount,
            entry_price=signal.entry,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            status="OPEN",
            entry_reason=signal.reason,
        )
        session.add(trade)
        session.flush()

        image_payload = {
            "trade_id": trade.id,
            "symbol": trade.symbol,
            "strategy": trade.strategy,
            "side": trade.side,
            "entry": trade.entry_price,
            "stop_loss": trade.stop_loss,
            "take_profit": trade.take_profit,
            "status": "OPEN",
        }
        trade.screenshot_entry = screenshots.capture_pre_entry(str(trade.id), image_payload)
        session.add(trade)

        runtime_state["open_trades"][symbol] = {
            "trade_id": trade.id,
            "side": trade.side,
            "entry": trade.entry_price,
            "stop_loss": trade.stop_loss,
            "take_profit": trade.take_profit,
            "risk_amount": trade.risk_amount,
        }

    day_key = datetime.now(UTC).strftime("%Y-%m-%d")
    stats = runtime_state["day_stats"]
    stats["trades"][day_key] = int(stats["trades"].get(day_key, 0)) + 1

    journal.log_event(
        "trade_opened",
        {
            "symbol": signal.symbol,
            "side": signal.side,
            "entry": signal.entry,
            "sl": signal.stop_loss,
            "tp": signal.take_profit,
            "quantity": decision.quantity,
        },
        symbol=signal.symbol,
        strategy=signal.strategy,
    )
    await telegram.send(
        telegram.format_open(
            {
                "side": signal.side,
                "symbol": signal.symbol,
                "entry": signal.entry,
                "sl": signal.stop_loss,
                "tp": signal.take_profit,
                "risk_pct": settings.risk_per_trade_pct,
                "reason": signal.reason,
            }
        )
    )


async def _check_open_trade_exit(symbol: str, candle: dict[str, Any]) -> None:
    open_trade = runtime_state["open_trades"].get(symbol)
    if open_trade is None:
        return

    side = open_trade["side"].lower()
    high = float(candle["high"])
    low = float(candle["low"])

    hit_sl = low <= open_trade["stop_loss"] if side == "buy" else high >= open_trade["stop_loss"]
    hit_tp = high >= open_trade["take_profit"] if side == "buy" else low <= open_trade["take_profit"]

    if not hit_sl and not hit_tp:
        return

    exit_price = open_trade["stop_loss"] if hit_sl else open_trade["take_profit"]
    reason = "sl_hit" if hit_sl else "tp_hit"
    sign = 1 if side == "buy" else -1
    pnl = sign * (exit_price - open_trade["entry"]) * open_trade.get("risk_amount", 1.0)
    pnl_r = risk_manager.compute_r_multiple(
        side=side,
        entry=open_trade["entry"],
        stop=open_trade["stop_loss"],
        exit_price=exit_price,
    )

    trade_id = int(open_trade["trade_id"])
    with db_session() as session:
        trade = session.get(Trade, trade_id)
        if trade is None:
            runtime_state["open_trades"].pop(symbol, None)
            return

        trade.exit_price = exit_price
        trade.status = "CLOSED"
        trade.pnl = round(pnl, 2)
        trade.pnl_r = round(pnl_r, 3)
        trade.exit_reason = reason
        trade.closed_at = datetime.utcnow()
        trade.screenshot_exit = screenshots.capture_post_exit(
            str(trade.id),
            {
                "trade_id": trade.id,
                "symbol": trade.symbol,
                "entry": trade.entry_price,
                "exit": trade.exit_price,
                "pnl": trade.pnl,
                "result": reason,
            },
        )
        session.add(trade)

    runtime_state["equity"] = float(runtime_state["equity"]) + pnl
    if pnl < 0:
        day_key = datetime.now(UTC).strftime("%Y-%m-%d")
        day_loss = float(runtime_state["day_stats"]["loss"].get(day_key, 0.0))
        runtime_state["day_stats"]["loss"][day_key] = round(day_loss + abs(pnl), 2)

    runtime_state["open_trades"].pop(symbol, None)

    journal.log_event(
        "trade_closed",
        {
            "trade_id": trade_id,
            "symbol": symbol,
            "exit": exit_price,
            "reason": reason,
            "pnl": round(pnl, 2),
            "pnl_r": round(pnl_r, 3),
        },
        symbol=symbol,
        strategy="range_reentry_4h",
    )
    await telegram.send(
        telegram.format_close(
            {
                "symbol": symbol,
                "result": reason,
                "pnl": round(pnl, 2),
                "pnl_r": round(pnl_r, 3),
                "balance": round(float(runtime_state["equity"]), 2),
            }
        )
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Starting backend services")
    init_db()
    login_result = await exchange.login(settings.delta_api_key, settings.delta_api_secret)
    logger.info("Exchange startup auth status=%s mode=%s", login_result.get("status"), login_result.get("mode", "paper"))

    if settings.live_trading_enabled:
        if exchange.is_paper() and settings.require_live_exchange_when_enabled:
            raise RuntimeError(
                "LIVE_TRADING_ENABLED=true but exchange is in paper mode. "
                "Set valid API credentials and permissions, or disable LIVE_TRADING_ENABLED."
            )
        logger.warning("Live trading is ENABLED")
    else:
        logger.warning("Live trading is DISABLED (LIVE_TRADING_ENABLED=false)")

    candles.subscribe(process_candle)
    await candles.start()
    scheduler.start()
    yield
    scheduler.shutdown()
    await candles.stop()
    logger.info("Stopped backend services")


app = FastAPI(title=settings.app_name, version="0.3.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}


@app.post("/auth/login")
async def login(payload: LoginRequest) -> dict:
    result = await exchange.login(payload.api_key, payload.api_secret)
    logger.info("Login status=%s", result["status"])
    return result


@app.get("/account/balance")
async def account_balance() -> dict:
    try:
        balance = await exchange.get_balance()
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    balance["sim_equity"] = round(float(runtime_state["equity"]), 2)
    return balance


@app.get("/positions/open")
async def open_positions() -> list[dict]:
    with db_session() as session:
        rows = session.execute(select(Trade).where(Trade.status == "OPEN")).scalars().all()
    return [
        {
            "id": t.id,
            "symbol": t.symbol,
            "strategy": t.strategy,
            "side": t.side,
            "entry": t.entry_price,
            "sl": t.stop_loss,
            "tp": t.take_profit,
            "quantity": t.quantity,
            "opened_at": t.opened_at.isoformat(),
        }
        for t in rows
    ]


@app.post("/orders")
async def place_order(payload: PlaceOrderRequest) -> dict:
    if exchange.is_paper() is False and settings.live_trading_enabled is False:
        raise HTTPException(
            status_code=403,
            detail="Live order blocked. Set LIVE_TRADING_ENABLED=true to allow live execution.",
        )
    try:
        return await orders.place(payload.model_dump())
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.delete("/orders/{order_id}")
async def cancel_order(order_id: str) -> dict:
    try:
        return await orders.cancel(order_id)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/candles/latest/{symbol}")
async def latest_candle(symbol: str) -> dict:
    candle = candles.get_latest(symbol)
    if candle is None:
        raise HTTPException(status_code=404, detail=f"No candle available for {symbol}")
    return candle


@app.get("/candles/history/{symbol}")
async def candle_history(symbol: str, limit: int = 500) -> list[dict]:
    return candles.get_history(symbol, limit)


@app.get("/strategy/status")
async def strategy_status(symbol: str | None = None) -> dict:
    return strategy_engine.status(symbol=symbol)


@app.get("/strategies")
async def list_strategies() -> list[dict]:
    return strategy_engine.registry.list()


@app.put("/strategies/{name}")
async def toggle_strategy(name: str, payload: StrategyToggleRequest) -> dict:
    try:
        result = strategy_engine.registry.set_enabled(name, payload.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    journal.log_event("strategy_toggled", {"name": name, "enabled": payload.enabled}, strategy=name)
    return result


@app.get("/dashboard/overview")
async def dashboard_overview() -> dict:
    metrics = analytics.dashboard_metrics()
    metrics["balance"] = round(float(runtime_state["equity"]), 2)
    metrics["symbols"] = [
        {"symbol": symbol, "status": data.get("last_status", "idle")}
        for symbol, data in runtime_state["status_by_symbol"].items()
    ]
    return metrics


@app.get("/dashboard/equity")
async def dashboard_equity(limit: int = 200) -> list[dict]:
    return analytics.equity_curve(limit=limit)


@app.get("/dashboard/trades")
async def dashboard_trades(limit: int = 200) -> list[dict]:
    return analytics.trade_history(limit=limit)


@app.get("/dashboard/heatmap")
async def dashboard_heatmap() -> list[dict]:
    return analytics.heatmap()


@app.get("/dashboard/strategy-stats")
async def dashboard_strategy_stats() -> dict:
    return analytics.strategy_statistics()


@app.get("/dashboard/calendar")
async def dashboard_calendar(days: int = 60) -> list[dict]:
    return analytics.daily_calendar(days=days)


@app.get("/journal/events")
async def journal_events(limit: int = 300) -> list[dict]:
    return analytics.event_timeline(limit=limit)


@app.get("/journal/weekly-ai-summary")
async def weekly_ai_summary() -> dict:
    return analytics.weekly_ai_summary()


@app.get("/replay/{trade_id}")
async def replay_trade(trade_id: int) -> dict:
    try:
        return analytics.replay(trade_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/backtest/run")
async def run_backtest(payload: BacktestRequest) -> dict:
    candles_data = candles.get_history(payload.symbol, payload.lookback)
    if len(candles_data) < 100:
        raise HTTPException(status_code=400, detail="Not enough candle history for backtest")
    return backtest_service.run(
        candles_data,
        rr=payload.rr,
        reentry_buffer_pct=payload.reentry_buffer_pct,
    )


@app.post("/backtest/optimize")
async def optimize_backtest(payload: OptimizeRequest) -> dict:
    candles_data = candles.get_history(payload.symbol, payload.lookback)
    if len(candles_data) < 100:
        raise HTTPException(status_code=400, detail="Not enough candle history for optimization")
    rr_values = [value for value in payload.rr_values if value > 0]
    return backtest_service.optimize(
        candles_data,
        rr_values=rr_values,
        reentry_buffer_pct=payload.reentry_buffer_pct,
    )


@app.websocket("/ws/candles/{symbol}")
async def candles_socket(ws: WebSocket, symbol: str) -> None:
    await ws.accept()
    try:
        while True:
            candle = candles.get_latest(symbol)
            if candle is not None:
                await ws.send_json(candle)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        logger.info("Websocket disconnected symbol=%s", symbol.upper())


@app.websocket("/ws/dashboard")
async def dashboard_socket(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            snapshot = {
                "overview": await dashboard_overview(),
                "equity": analytics.equity_curve(limit=80),
                "open_positions": await open_positions(),
            }
            await ws.send_json(snapshot)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        logger.info("Dashboard websocket disconnected")
