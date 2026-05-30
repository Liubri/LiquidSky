"""Normalizing Kalshi's dollar-string and fixed-point fields."""
from datetime import date

from liquidsky import marketdata


def test_parse_event_date_from_ticker():
    assert marketdata.parse_event_date("KXHIGHNY-26MAY30") == date(2026, 5, 30)
    assert marketdata.parse_event_date("KXHIGHNY-26MAY30-B72.5") == date(2026, 5, 30)
    assert marketdata.parse_event_date("KXHIGHCHI-26JAN05-T40") == date(2026, 1, 5)
    assert marketdata.parse_event_date("no-date-here") is None
    assert marketdata.parse_event_date(None) is None


def test_price_cents_from_dollar_strings():
    m = {"yes_ask_dollars": "0.0600", "no_bid_dollars": "0.9400"}
    assert marketdata.price_cents(m, "yes_ask") == 6
    assert marketdata.price_cents(m, "no_bid") == 94


def test_price_cents_none_for_empty_or_zero():
    assert marketdata.price_cents({"yes_ask_dollars": ""}, "yes_ask") is None
    assert marketdata.price_cents({"yes_ask_dollars": "0.0000"}, "yes_ask") is None
    assert marketdata.price_cents({}, "yes_ask") is None


def test_price_cents_falls_back_to_plain_integer():
    assert marketdata.price_cents({"yes_ask": 30}, "yes_ask") == 30


def test_volume_prefers_24h_fixed_point():
    m = {"volume_24h_fp": "8698.28", "volume_fp": "181762.88"}
    assert marketdata.volume(m) == 8698.28
    assert marketdata.volume({"volume_fp": "100.0"}) == 100.0
    assert marketdata.volume({}) == 0.0
