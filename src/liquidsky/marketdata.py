"""Normalizers for Kalshi market JSON fields.

The live API expresses prices as dollar *strings* (e.g. "0.0600" = 6 cents) in
`*_dollars` fields, and volume/open-interest as fixed-point *strings* in `*_fp`
fields. The plain integer fields (`yes_ask`, `volume`, ...) are often null. These
helpers convert to the integer cents / float counts the rest of the bot uses.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Optional

_MONTHS = {
    abbr: i
    for i, abbr in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
         "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"],
        start=1,
    )
}
# Date code embedded in tickers/event tickers, e.g. "26MAY30" -> 2026-05-30.
_DATE_RE = re.compile(r"(\d{2})([A-Z]{3})(\d{2})")


def parse_event_date(text: Optional[str]) -> Optional[date]:
    """Parse the trading-day date from a ticker / event_ticker, or None.

    The measurement date is encoded in the ticker (e.g. "KXHIGHNY-26MAY30");
    it is *not* the same as the market's close_time, which falls just after
    midnight the following day.
    """
    if not text:
        return None
    m = _DATE_RE.search(text.upper())
    if not m:
        return None
    yy, mon, dd = m.groups()
    month = _MONTHS.get(mon)
    if month is None:
        return None
    try:
        return date(2000 + int(yy), month, int(dd))
    except ValueError:
        return None


def price_cents(market: Dict[str, Any], base: str) -> Optional[int]:
    """Read a price field (e.g. base='yes_ask') as integer cents, or None.

    Prefers the `{base}_dollars` string field; falls back to a plain integer
    `{base}` field if present.
    """
    raw = market.get(f"{base}_dollars")
    if raw is not None and raw != "":
        cents = round(float(raw) * 100)
        return cents if cents > 0 else None
    raw = market.get(base)
    if raw is not None:
        cents = int(raw)
        return cents if cents > 0 else None
    return None


def volume(market: Dict[str, Any]) -> float:
    """Best available trading volume (prefers 24h), as a float count."""
    for key in ("volume_24h_fp", "volume_fp", "volume_24h", "volume"):
        raw = market.get(key)
        if raw not in (None, ""):
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
    return 0.0
