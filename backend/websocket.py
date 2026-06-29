import asyncio
import contextlib
import random
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable


CandleCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class Candle:
    symbol: str
    timeframe: str
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class CandleStream:
    def __init__(self) -> None:
        self._latest: dict[str, Candle] = {}
        self._history: dict[str, deque[dict[str, Any]]] = {}
        self._running = False
        self._task: asyncio.Task[Any] | None = None
        self._callbacks: list[CandleCallback] = []

    def subscribe(self, callback: CandleCallback) -> None:
        self._callbacks.append(callback)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
        seed_price = {
            "BTCUSDT": 100000.0,
            "ETHUSDT": 3500.0,
            "SOLUSDT": 150.0,
            "BNBUSDT": 600.0,
            "XRPUSDT": 0.65,
        }

        while self._running:
            for symbol in symbols:
                base = self._latest.get(symbol)
                open_price = base.close if base else seed_price[symbol]
                drift = random.uniform(-0.004, 0.004)
                close_price = round(open_price * (1 + drift), 4)
                high_price = max(open_price, close_price) * (1 + random.uniform(0, 0.0015))
                low_price = min(open_price, close_price) * (1 - random.uniform(0, 0.0015))
                candle = Candle(
                    symbol=symbol,
                    timeframe="5m",
                    ts=datetime.now(UTC).isoformat(),
                    open=round(open_price, 4),
                    high=round(high_price, 4),
                    low=round(low_price, 4),
                    close=close_price,
                    volume=round(random.uniform(20, 200), 2),
                )
                self._latest[symbol] = candle
                serialized = asdict(candle)
                if symbol not in self._history:
                    self._history[symbol] = deque(maxlen=5000)
                self._history[symbol].append(serialized)

                for callback in self._callbacks:
                    await callback(serialized)

            await asyncio.sleep(1)

    def get_latest(self, symbol: str) -> dict[str, Any] | None:
        candle = self._latest.get(symbol.upper())
        return asdict(candle) if candle else None

    def get_history(self, symbol: str, limit: int = 500) -> list[dict[str, Any]]:
        history = self._history.get(symbol.upper())
        if not history:
            return []
        safe_limit = min(max(limit, 1), 5000)
        return list(history)[-safe_limit:]
