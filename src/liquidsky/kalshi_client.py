"""Kalshi REST client with RSA-PSS request signing.

Authentication scheme (per docs.kalshi.com):
  - Headers: KALSHI-ACCESS-KEY, KALSHI-ACCESS-TIMESTAMP (unix ms),
    KALSHI-ACCESS-SIGNATURE.
  - Signature = base64( RSA-PSS-SHA256( f"{ts_ms}{METHOD}{path}" ) ) where `path`
    is the request path *without* query parameters, and PSS uses MGF1-SHA256 with
    salt length = digest length (32 bytes).

Market-data reads are public and work without credentials, so paper mode can use
real prices without an API key. Portfolio/order calls require signing.
"""
from __future__ import annotations

import base64
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class KalshiError(RuntimeError):
    """Raised when the Kalshi API returns a non-2xx response."""


class KalshiClient:
    def __init__(
        self,
        base_url: str,
        api_key_id: Optional[str] = None,
        private_key_path: Optional[str] = None,
        timeout: float = 20.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key_id = api_key_id
        self.timeout = timeout
        self._private_key: Optional[rsa.RSAPrivateKey] = None
        if private_key_path:
            self._private_key = self._load_private_key(private_key_path)
        self._session = requests.Session()

    # ------------------------------------------------------------------ auth
    @staticmethod
    def _load_private_key(path: str) -> rsa.RSAPrivateKey:
        with open(path, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise KalshiError("Provided key is not an RSA private key")
        return key

    @staticmethod
    def sign_message(private_key: rsa.RSAPrivateKey, message: str) -> str:
        """Sign `message` with RSA-PSS/SHA256 and return base64 (testable, static)."""
        signature = private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")

    def _auth_headers(self, method: str, path: str) -> Dict[str, str]:
        if not (self.api_key_id and self._private_key):
            raise KalshiError(
                "This request requires authentication; set KALSHI_API_KEY_ID and "
                "KALSHI_PRIVATE_KEY_PATH."
            )
        ts_ms = str(int(time.time() * 1000))
        # Sign the path WITHOUT query parameters.
        path_no_query = urlsplit(path).path
        msg = f"{ts_ms}{method.upper()}{path_no_query}"
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts_ms,
            "KALSHI-ACCESS-SIGNATURE": self.sign_message(self._private_key, msg),
        }

    # --------------------------------------------------------------- request
    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        auth: bool = False,
    ) -> Dict[str, Any]:
        # The signed path must include the API version prefix but not the host.
        signed_path = urlsplit(self.base_url).path + path
        url = self.base_url + path
        headers = {"Content-Type": "application/json"}
        if auth:
            headers.update(self._auth_headers(method, signed_path))

        resp = self._session.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=self.timeout,
        )
        if not resp.ok:
            raise KalshiError(
                f"{method} {path} -> {resp.status_code}: {resp.text[:500]}"
            )
        if resp.content:
            return resp.json()
        return {}

    # --------------------------------------------------------- market data
    def get_series(self, series_ticker: str) -> Dict[str, Any]:
        return self._request("GET", f"/series/{series_ticker}")

    def get_series_list(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"category": category} if category else None
        data = self._request("GET", "/series", params=params)
        return data.get("series", [])

    def get_markets(
        self,
        series_ticker: Optional[str] = None,
        event_ticker: Optional[str] = None,
        status: Optional[str] = "open",
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Return all markets matching the filter, following pagination cursors."""
        params: Dict[str, Any] = {"limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if status:
            params["status"] = status

        markets: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            if cursor:
                params["cursor"] = cursor
            data = self._request("GET", "/markets", params=params)
            markets.extend(data.get("markets", []))
            cursor = data.get("cursor")
            if not cursor:
                break
        return markets

    def get_market(self, ticker: str) -> Dict[str, Any]:
        return self._request("GET", f"/markets/{ticker}").get("market", {})

    def get_event(self, event_ticker: str) -> Dict[str, Any]:
        return self._request("GET", f"/events/{event_ticker}")

    def get_orderbook(self, ticker: str, depth: int = 10) -> Dict[str, Any]:
        data = self._request(
            "GET", f"/markets/{ticker}/orderbook", params={"depth": depth}
        )
        return data.get("orderbook", {})

    # ------------------------------------------------------------- portfolio
    def get_balance(self) -> Dict[str, Any]:
        return self._request("GET", "/portfolio/balance", auth=True)

    def get_positions(self) -> Dict[str, Any]:
        return self._request("GET", "/portfolio/positions", auth=True)

    def create_order(
        self,
        ticker: str,
        action: str,
        side: str,
        count: int,
        client_order_id: str,
        order_type: str = "limit",
        yes_price: Optional[int] = None,
        no_price: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Place an order. Prices are integer cents (1-99).

        For a limit order you supply the price on the side you're trading:
        buying YES -> yes_price; buying NO -> no_price.
        """
        body: Dict[str, Any] = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "count": count,
            "type": order_type,
            "client_order_id": client_order_id,
        }
        if yes_price is not None:
            body["yes_price"] = yes_price
        if no_price is not None:
            body["no_price"] = no_price
        return self._request("POST", "/portfolio/orders", json_body=body, auth=True)

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        return self._request("DELETE", f"/portfolio/orders/{order_id}", auth=True)
