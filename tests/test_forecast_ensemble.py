"""Ensemble forecast: empirical bucket probability + NWS anchoring (no network)."""
import statistics
from datetime import date

from liquidsky import forecast_ensemble as fe
from liquidsky.cities import CITIES
from liquidsky.strategy import Bucket

NEG_INF = float("-inf")
POS_INF = float("inf")
_CITY = CITIES["KXHIGHNY"]
_DAY = date(2026, 6, 2)


def test_empirical_probability_is_fraction_in_bucket():
    members = [70, 71, 72, 73, 74]
    # [71.5, 73.5) contains 72 and 73 -> 2/5.
    assert fe.empirical_bucket_probability(members, Bucket(71.5, 73.5)) == 0.4


def test_empirical_probability_open_tails_partition():
    members = [68, 70, 72]
    left = fe.empirical_bucket_probability(members, Bucket(NEG_INF, 71.0))
    right = fe.empirical_bucket_probability(members, Bucket(71.0, POS_INF))
    assert left == 2 / 3 and right == 1 / 3
    assert left + right == 1.0


def test_empirical_probability_empty_is_zero():
    assert fe.empirical_bucket_probability([], Bucket(0.0, 1.0)) == 0.0


def test_build_anchors_toward_nws_and_preserves_spread(monkeypatch):
    monkeypatch.setattr(fe, "fetch_ensemble_member_highs", lambda *a, **k: [70.0, 72.0, 74.0])
    monkeypatch.setattr(fe, "fetch_nws_high", lambda *a, **k: 78.0)

    fc = fe.build_ensemble_forecast(_CITY, _DAY, nws_weight=0.5)
    # target mu = 0.5*72 + 0.5*78 = 75 -> shift = +3 from raw mean 72.
    assert fc.mu == 75.0
    assert fc.anchor_shift == 3.0
    assert fc.nws_high_f == 78.0
    assert fc.n_members == 3
    # Shifting every member preserves the ensemble's shape (spread).
    assert round(statistics.stdev(fc.members), 6) == round(statistics.stdev([70, 72, 74]), 6)


def test_build_without_nws_leaves_members_untouched(monkeypatch):
    monkeypatch.setattr(fe, "fetch_ensemble_member_highs", lambda *a, **k: [70.0, 72.0, 74.0])
    monkeypatch.setattr(fe, "fetch_nws_high", lambda *a, **k: None)

    fc = fe.build_ensemble_forecast(_CITY, _DAY)
    assert fc.mu == 72.0
    assert fc.anchor_shift == 0.0
    assert fc.nws_high_f is None


def test_build_returns_none_when_no_members(monkeypatch):
    monkeypatch.setattr(fe, "fetch_ensemble_member_highs", lambda *a, **k: [])
    # Should short-circuit before touching NWS.
    monkeypatch.setattr(fe, "fetch_nws_high", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert fe.build_ensemble_forecast(_CITY, _DAY) is None
