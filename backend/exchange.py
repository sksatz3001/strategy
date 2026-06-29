import hashlib
import hmac
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from backend.config import settings

try:
    from delta_rest_client import DeltaRestClient
except Exception:
    DeltaRestClient = None


@dataclass
class Position:
    symbol: str
    side: str
    quantity: float
    entry_price: float
    unrealized_pnl: float


@dataclass
class Order:
    id: str
    symbol: str
    side: str
    quantity: float
    order_type: str
    price: float | None
    status: str
    created_at: str


class ExchangeService:
    def __init__(self) -> None:
        self._logged_in = False
        self._paper_mode = True
        self._balance = settings.default_equity
        self._positions: list[Position] = []
        self._orders: dict[str, Order] = {}

        self._api_key = settings.delta_api_key
        self._api_secret = settings.delta_api_secret
        self._base_url = settings.delta_base_url.rstrip("/")
        self._default_product_id = settings.delta_product_id_default

        self._symbol_product_cache: dict[str, int] = {}
        self._delta_client: Any | None = None

    def mode(self) -> str:
        return "paper" if self._paper_mode else "live"

    def is_paper(self) -> bool:
        return self._paper_mode

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.upper().replace("USDT", "USD")

    def _signature_headers(self, method: str, path: str, query_string: str = "", body: str = "") -> dict[str, str]:
        timestamp = str(int(datetime.now(UTC).timestamp()))
        payload = method.upper() + timestamp + path + query_string + body
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "api-key": self._api_key,
            "timestamp": timestamp,
            "signature": signature,
            "User-Agent": "delta-bot/0.5",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        query_string = ""
        if params:
            query_string = "?" + urlencode(params)
        body = json.dumps(json_payload) if json_payload else ""

        headers = {
            "User-Agent": "delta-bot/0.5",
            "Content-Type": "application/json",
        }
        if authenticated:
            headers = self._signature_headers(method, path, query_string, body)

        async with httpx.AsyncClient(base_url=self._base_url, timeout=15.0) as client:
            response = await client.request(
                method,
                path,
                params=params,
                content=body,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                return {"result": data}
            return data

    def _init_sdk(self) -> None:
        if DeltaRestClient is None:
            self._delta_client = None
            return
        if not self._api_key or not self._api_secret:
            self._delta_client = None
            return
        try:
            self._delta_client = DeltaRestClient(
                base_url=self._base_url,
                api_key=self._api_key,
                api_secret=self._api_secret,
            )
        except Exception:
            self._delta_client = None

    async def _resolve_product_id(self, symbol: str) -> int:
        normalized = self._normalize_symbol(symbol)
        cached = self._symbol_product_cache.get(normalized)
        if cached:
            return cached

        try:
            response = await self._request("GET", f"/v2/products/{normalized}", authenticated=False)
            result = response.get("result", {})
            product_id = int(result.get("id", self._default_product_id))
            self._symbol_product_cache[normalized] = product_id
            return product_id
        except Exception:
            self._symbol_product_cache[normalized] = self._default_product_id
            return self._default_product_id

    async def login(self, api_key: str, api_secret: str) -> dict[str, Any]:
        if api_key:
            self._api_key = api_key
        if api_secret:
            self._api_secret = api_secret

        if not self._api_key or not self._api_secret:
            self._paper_mode = True
            self._logged_in = True
            return {
                "status": "paper-mode",
                "mode": "paper",
                "message": "Missing API credentials. Running in paper mode.",
            }

        self._init_sdk()

        try:
            await self._request("GET", "/v2/wallet/balances", authenticated=True)
            self._paper_mode = False
            self._logged_in = True
            return {
                "status": "ok",
                "mode": "live",
                "message": "Authenticated against Delta API",
                "transport": "signed-rest",
            }
        except Exception as exc:
            self._paper_mode = True
            self._logged_in = True
            return {
                "status": "paper-fallback",
                "mode": "paper",
                "message": f"Live auth failed ({exc}). Falling back to paper mode.",
            }

    def ensure_auth(self) -> None:
        if not self._logged_in:
            raise PermissionError("Not authenticated. Call /auth/login first.")

    async def get_balance(self) -> dict[str, float | str]:
        self.ensure_auth()
        if self._paper_mode:
            return {"equity": self._balance, "available": self._balance, "mode": "paper"}

        response = await self._request("GET", "/v2/wallet/balances", authenticated=True)
        rows = response.get("result", [])

        total = 0.0
        for row in rows:
            value = row.get("balance", 0)
            try:
                total += float(value)
            except (TypeError, ValueError):
                continue

        return {"equity": round(total, 4), "available": round(total, 4), "mode": "live"}

    async def get_open_positions(self) -> list[dict[str, Any]]:
        self.ensure_auth()
        if self._paper_mode:
            return [asdict(position) for position in self._positions]

        response = await self._request("GET", "/v2/positions/margined", authenticated=True)
        rows = response.get("result", [])

        parsed: list[dict[str, Any]] = []
        for row in rows:
            try:
                size = float(row.get("size", 0) or 0)
            except (TypeError, ValueError):
                size = 0.0
            if size == 0:
                continue
            parsed.append(
                {
                    "symbol": row.get("product_symbol") or row.get("symbol", ""),
                    "side": "buy" if size > 0 else "sell",
                    "quantity": abs(size),
                    "entry_price": float(row.get("entry_price", 0) or 0),
                    "unrealized_pnl": float(row.get("realized_pnl", 0) or 0),
                }
            )
        return parsed

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "market",
        price: float | None = None,
    ) -> dict[str, Any]:
        self.ensure_auth()

        normalized_symbol = self._normalize_symbol(symbol)

        if self._paper_mode:
            order_id = str(uuid4())
            order = Order(
                id=order_id,
                symbol=normalized_symbol,
                side=side,
                quantity=quantity,
                order_type=order_type,
                price=price,
                status="FILLED" if order_type == "market" else "OPEN",
                created_at=datetime.utcnow().isoformat(),
            )
            self._orders[order_id] = order

            if order.status == "FILLED":
                reference_price = float(price or 100.0)
                self._positions.append(
                    Position(
                        symbol=normalized_symbol,
                        side=side,
                        quantity=quantity,
                        entry_price=reference_price,
                        unrealized_pnl=0.0,
                    )
                )
            return asdict(order)

        product_id = await self._resolve_product_id(normalized_symbol)
        payload = {
            "product_id": product_id,
            "size": max(1, int(round(quantity))),
            "side": side.lower(),
            "order_type": "market_order" if order_type.lower() == "market" else "limit_order",
        }
        if price is not None:
            payload["limit_price"] = str(price)

        response = await self._request("POST", "/v2/orders", json_payload=payload, authenticated=True)
        result = response.get("result", {})

        return {
            "id": str(result.get("id", "")),
            "symbol": str(result.get("product_symbol", normalized_symbol)),
            "side": side.lower(),
            "quantity": float(result.get("size", payload["size"])),
            "order_type": order_type.lower(),
            "price": price,
            "status": str(result.get("state", "OPEN")).upper(),
            "created_at": datetime.utcnow().isoformat(),
            "mode": "live",
        }

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        self.ensure_auth()

        if self._paper_mode:
            order = self._orders.get(order_id)
            if order is None:
                raise KeyError(f"Order {order_id} not found")
            order.status = "CANCELED"
            return asdict(order)

        try:
            order_int = int(order_id)
        except ValueError as exc:
            raise KeyError(f"Invalid live order id: {order_id}") from exc

        payload = {
            "id": order_int,
            "product_id": self._default_product_id,
        }
        response = await self._request("DELETE", "/v2/orders", json_payload=payload, authenticated=True)
        result = response.get("result", {})

        return {
            "id": str(result.get("id", order_id)),
            "status": str(result.get("state", "CANCELED")).upper(),
            "mode": "live",
        }
