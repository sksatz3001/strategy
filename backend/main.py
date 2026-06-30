import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.analytics import AnalyticsService
from backend.backtest import BacktestService
from backend.config import settings
from backend.database import CandleRecord, Trade, db_session, init_db
from backend.exchange import ExchangeService
from backend.journal import JournalService
from backend.llm import LLMDecisionService
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
strategy_engine = StrategyEngine(
    ema_fast=settings.ema_fast,
    ema_slow=settings.ema_slow,
    ema_trend=settings.ema_trend,
    atr_period=settings.atr_period,
    volume_period=settings.volume_period,
    ema_slope_lookback=settings.ema_slope_lookback,
    atr_stop_mult=settings.atr_stop_mult,
    rr_tp1=settings.rr_tp1,
    rr_tp2=settings.rr_tp2,
    min_atr_pct=settings.min_atr_pct,
    max_distance_atr_mult=settings.max_distance_atr_mult,
    cooldown_candles=settings.cooldown_candles,
)
risk_manager = RiskManager(
    RiskConfig(
        risk_per_trade_pct=settings.risk_per_trade_pct,
        daily_loss_limit_pct=settings.daily_loss_limit_pct,
        daily_profit_target=settings.daily_profit_target,
        max_trades_per_day=settings.max_trades_per_day,
        max_leverage=settings.max_leverage,
    )
)
analytics = AnalyticsService()
journal = JournalService()
screenshots = ScreenshotService()
backtest_service = BacktestService()
telegram = TelegramService()
llm = LLMDecisionService()

runtime_state: dict[str, Any] = {
    "equity": settings.default_equity,
    "leverage": settings.default_leverage,
    "day_stats": {"trades": {}, "loss": {}},
    "open_trades": {},
    "status_by_symbol": {},
}


def _normalize_symbol(symbol: str) -> str:
    return symbol.upper().replace("USDT", "USD")


async def _combined_open_positions() -> list[dict[str, Any]]:
    with db_session() as session:
        db_rows = session.execute(select(Trade).where(Trade.status == "OPEN")).scalars().all()

    combined: list[dict[str, Any]] = [
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
            "source": "db",
        }
        for t in db_rows
    ]

    db_symbols = {_normalize_symbol(str(item["symbol"])) for item in combined}

    try:
        live_positions = await exchange.get_open_positions()
    except PermissionError:
        return combined
    except Exception:
        return combined

    synthetic_id = -1
    for position in live_positions:
        symbol = str(position.get("symbol", ""))
        normalized = _normalize_symbol(symbol)
        if normalized in db_symbols:
            continue

        combined.append(
            {
                "id": synthetic_id,
                "symbol": symbol,
                "strategy": "live_exchange",
                "side": str(position.get("side", "")),
                "entry": float(position.get("entry_price", 0.0) or 0.0),
                "sl": 0.0,
                "tp": 0.0,
                "quantity": float(position.get("quantity", 0.0) or 0.0),
                "opened_at": datetime.utcnow().isoformat(),
                "source": "exchange",
            }
        )
        synthetic_id -= 1

    return combined


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

    # Use live balance when available
    trade_equity = float(runtime_state["equity"])
    try:
        account = await exchange.get_balance()
        if str(account.get("mode", "paper")) == "live":
            trade_equity = float(account.get("equity", trade_equity))
    except Exception:
        pass

    decision = risk_manager.evaluate_entry(
        equity=trade_equity,
        day_stats=runtime_state["day_stats"],
        entry_price=signal.entry,
        stop_price=signal.stop_loss,
        leverage=int(runtime_state["leverage"]),
        now=datetime.now(UTC),
    )

    if not decision.approved:
        strategy_engine.reset_trade(symbol)
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
        strategy_engine.reset_trade(symbol)
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

    # LLM news sentiment check before entry
    if llm.is_enabled() and settings.llm_news_check_enabled:
        news = await llm.check_news_sentiment(symbol)
        sentiment = news.get("sentiment", "neutral")
        if signal.side == "buy" and sentiment == "bearish":
            strategy_engine.reset_trade(symbol)
            journal.log_event("llm_blocked_entry", {"symbol": symbol, "side": "buy", "news": news}, level="WARN", symbol=symbol, strategy=signal.strategy)
            await telegram.send(f"🤖 LLM blocked BUY {symbol}: bearish news sentiment")
            return
        if signal.side == "sell" and sentiment == "bullish":
            strategy_engine.reset_trade(symbol)
            journal.log_event("llm_blocked_entry", {"symbol": symbol, "side": "sell", "news": news}, level="WARN", symbol=symbol, strategy=signal.strategy)
            await telegram.send(f"🤖 LLM blocked SELL {symbol}: bullish news sentiment")
            return

    try:
        await orders.place(
            {
                "symbol": signal.symbol,
                "side": signal.side,
                "quantity": decision.quantity,
                "order_type": "market",
                "price": signal.entry,
            }
        )
    except Exception as exc:
        strategy_engine.reset_trade(symbol)
        journal.log_event(
            "order_failed",
            {"symbol": symbol, "error": str(exc), "strategy": signal.strategy},
            level="ERROR",
            symbol=symbol,
            strategy=signal.strategy,
        )
        await telegram.send(f"❌ Order failed {symbol}: {exc}")
        return

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
            "quantity": trade.quantity,
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
    close = float(candle["close"])

    # Sync SL from strategy state (breakeven move after TP1)
    strat_state = strategy_engine.ema_scalp.states.get(symbol)
    if strat_state and strat_state.in_trade:
        open_trade["stop_loss"] = strat_state.stop_loss
        open_trade["take_profit"] = strat_state.take_profit

    hit_sl = low <= open_trade["stop_loss"] if side == "buy" else high >= open_trade["stop_loss"]
    hit_tp = high >= open_trade["take_profit"] if side == "buy" else low <= open_trade["take_profit"]

    # Check trailed-out exit (EMA21 cross after TP1)
    trailed_out = False
    if strat_state and not strat_state.in_trade and strat_state.last_status in (
        "long_trailed_out", "short_trailed_out", "long_tp2_hit", "short_tp2_hit",
    ):
        trailed_out = True

    if not hit_sl and not hit_tp and not trailed_out:
        return

    trade_id = int(open_trade["trade_id"])

    if trailed_out:
        exit_price = close
        reason = "trailed_out"
    elif hit_sl:
        exit_price = open_trade["stop_loss"]
        reason = "sl_hit"
    else:
        exit_price = open_trade["take_profit"]
        reason = "tp_hit"
    sign = 1 if side == "buy" else -1
    pnl = sign * (exit_price - open_trade["entry"]) * open_trade.get("risk_amount", 1.0)
    pnl_r = risk_manager.compute_r_multiple(
        side=side,
        entry=open_trade["entry"],
        stop=open_trade["stop_loss"],
        exit_price=exit_price,
    )

    # Send actual close order to exchange
    close_side = "sell" if side == "buy" else "buy"
    quantity = float(open_trade.get("quantity", 0)) or 0.0

    # If quantity not in runtime state, get from trade DB
    if quantity <= 0:
        with db_session() as s:
            db_trade = s.get(Trade, trade_id)
            if db_trade:
                quantity = float(db_trade.quantity or 0)

    if quantity > 0:
        try:
            await orders.place({
                "symbol": symbol,
                "side": close_side,
                "quantity": quantity,
                "order_type": "market",
                "price": exit_price,
            })
            logger.info("Close order sent: %s %s qty=%s", symbol, close_side, quantity)
        except Exception as exc:
            logger.error("Close order failed for %s: %s", symbol, exc)
    else:
        logger.warning("No quantity available for closing %s", symbol)

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
    day_key = datetime.now(UTC).strftime("%Y-%m-%d")
    if pnl < 0:
        day_loss = float(runtime_state["day_stats"]["loss"].get(day_key, 0.0))
        runtime_state["day_stats"]["loss"][day_key] = round(day_loss + abs(pnl), 2)
    else:
        day_profit = float(runtime_state["day_stats"].get("profit", {}).get(day_key, 0.0))
        runtime_state["day_stats"].setdefault("profit", {})[day_key] = round(day_profit + pnl, 2)

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
        strategy="ema_scalp_9_21_200",
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/positions/close-all-exchange")
async def close_all_exchange_positions() -> dict:
    """Close all positions on Delta Exchange that DB thinks are closed but exchange still has open."""
    closed_symbols = []
    try:
        live_positions = await exchange.get_open_positions()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    for pos in live_positions:
        symbol = str(pos.get("symbol", ""))
        side = str(pos.get("side", ""))
        quantity = float(pos.get("quantity", 0))
        close_side = "sell" if side == "buy" else "buy"
        try:
            await orders.place({
                "symbol": symbol,
                "side": close_side,
                "quantity": quantity,
                "order_type": "market",
            })
            closed_symbols.append(symbol)
        except Exception as exc:
            logger.error("Failed to close %s: %s", symbol, exc)

    return {"closed": closed_symbols, "count": len(closed_symbols)}


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
    return await _combined_open_positions()


@app.post("/positions/close/{symbol}")
async def close_position(symbol: str) -> dict:
    symbol = symbol.upper()
    try:
        positions = await exchange.get_open_positions()
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    normalized = symbol.replace("USDT", "USD")
    target = None
    for pos in positions:
        pos_symbol = str(pos.get("symbol", "")).upper().replace("USDT", "USD")
        if pos_symbol == normalized:
            target = pos
            break

    if target is None:
        raise HTTPException(status_code=404, detail=f"No open position found for {symbol}")

    side = str(target.get("side", "buy")).lower()
    close_side = "sell" if side == "buy" else "buy"
    quantity = float(target.get("quantity", 0))

    if quantity <= 0:
        raise HTTPException(status_code=400, detail=f"Invalid quantity for {symbol}: {quantity}")

    try:
        result = await orders.place({
            "symbol": symbol,
            "side": close_side,
            "quantity": quantity,
            "order_type": "market",
        })
        journal.log_event("manual_close", {"symbol": symbol, "side": close_side, "quantity": quantity, "result": result}, symbol=symbol)
        return {"status": "close_order_sent", "symbol": symbol, "side": close_side, "quantity": quantity}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Close order failed: {exc}")


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
    sim_equity = round(float(runtime_state["equity"]), 2)
    live_equity = sim_equity
    account_mode = "paper"

    try:
        account = await exchange.get_balance()
        account_mode = str(account.get("mode", "paper"))
        if account_mode == "live":
            live_equity = round(float(account.get("equity", sim_equity)), 2)
    except PermissionError:
        account_mode = "unauthenticated"

    metrics["balance"] = live_equity
    metrics["sim_equity"] = sim_equity
    metrics["account_mode"] = account_mode
    metrics["open_positions"] = [
        {
            "id": item["id"],
            "symbol": item["symbol"],
            "side": item["side"],
            "entry": item["entry"],
            "sl": item["sl"],
            "tp": item["tp"],
            "strategy": item["strategy"],
        }
        for item in await _combined_open_positions()
    ]
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


@app.get("/dashboard/symbol-stats")
async def dashboard_symbol_stats() -> list[dict]:
    with db_session() as session:
        all_trades = session.execute(select(Trade)).scalars().all()

    from collections import defaultdict
    by_symbol: dict[str, list] = defaultdict(list)
    for t in all_trades:
        by_symbol[t.symbol].append(t)

    result = []
    for symbol, trades in sorted(by_symbol.items()):
        closed = [t for t in trades if t.status == "CLOSED"]
        open_t = [t for t in trades if t.status == "OPEN"]
        wins = [float(t.pnl or 0) for t in closed if float(t.pnl or 0) > 0]
        losses = [float(t.pnl or 0) for t in closed if float(t.pnl or 0) <= 0]
        total_pnl = sum(float(t.pnl or 0) for t in closed)
        result.append({
            "symbol": symbol,
            "status": runtime_state["status_by_symbol"].get(symbol, {}).get("last_status", "idle"),
            "total_trades": len(trades),
            "open_trades": len(open_t),
            "closed_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / max(len(closed), 1) * 100, 2),
            "total_pnl": round(total_pnl, 2),
            "total_profit": round(sum(wins), 2),
            "total_loss": round(sum(losses), 2),
            "avg_pnl": round(total_pnl / max(len(closed), 1), 2),
            "lots_traded": round(sum(float(t.quantity or 0) for t in trades), 4),
        })
    return result


@app.get("/llm/status")
async def llm_status() -> dict:
    return {
        "enabled": llm.is_enabled(),
        "news_check": settings.llm_news_check_enabled,
        "provider": settings.llm_provider,
        "model": settings.llm_model,
    }


@app.get("/llm/news/{symbol}")
async def llm_news_check(symbol: str) -> dict:
    return await llm.check_news_sentiment(symbol.upper())


@app.post("/llm/evaluate-exit/{trade_id}")
async def llm_evaluate_exit(trade_id: int) -> dict:
    with db_session() as session:
        trade = session.get(Trade, trade_id)
        if trade is None:
            raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
        trade_dict = {
            "symbol": trade.symbol,
            "side": trade.side,
            "entry": trade.entry_price,
            "sl": trade.stop_loss,
            "tp": trade.take_profit,
            "status": trade.status,
        }
    candle = candles.get_latest(trade_dict["symbol"])
    current_price = float(candle["close"]) if candle else trade_dict["entry"]
    history = candles.get_history(trade_dict["symbol"], 20)
    return await llm.evaluate_exit(trade_dict, current_price, history)


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
                "trades": analytics.trade_history(limit=20),
                "symbol_stats": await dashboard_symbol_stats(),
                "day_stats": {
                    "trades": runtime_state["day_stats"].get("trades", {}),
                    "loss": runtime_state["day_stats"].get("loss", {}),
                    "profit": runtime_state["day_stats"].get("profit", {}),
                    "profit_target": settings.daily_profit_target,
                    "loss_limit_pct": settings.daily_loss_limit_pct,
                },
            }
            await ws.send_json(snapshot)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        logger.info("Dashboard websocket disconnected")
