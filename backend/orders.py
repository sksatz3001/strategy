from typing import Any

from backend.exchange import ExchangeService


class OrderService:
    def __init__(self, exchange: ExchangeService) -> None:
        self.exchange = exchange

    async def place(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.exchange.place_order(
            symbol=payload["symbol"],
            side=payload["side"],
            quantity=payload["quantity"],
            order_type=payload.get("order_type", "market"),
            price=payload.get("price"),
        )

    async def cancel(self, order_id: str) -> dict[str, Any]:
        return await self.exchange.cancel_order(order_id)
