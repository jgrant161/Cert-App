import pytest

from consign import db, money


def test_to_cents():
    assert money.to_cents("$1,250.5") == 125050
    assert money.to_cents(" 19.999 ") == 2000
    assert money.to_cents("") is None
    with pytest.raises(ValueError):
        money.to_cents("abc")
    with pytest.raises(ValueError):
        money.to_cents("-5")
    assert money.to_cents("-5", allow_negative=True) == -500
    assert money.fmt(-123456) == "-$1,234.56"


def test_tiers_pick_the_highest_reached():
    tiers = [[0, 30], [100, 25], [500, 20]]
    assert money.tier_pct(9999, tiers) == 30
    assert money.tier_pct(10000, tiers) == 25
    assert money.tier_pct(500000, tiers) == 20


def test_item_rate_beats_consignor_rate_beats_tiers():
    assert money.commission_pct({"commission_pct": 10, "consignor_pct": 20}, 100) == 10
    assert money.commission_pct({"commission_pct": None, "consignor_pct": 20}, 100) == 20
    assert money.commission_pct({"commission_pct": None, "consignor_pct": None}, 100) == 30


@pytest.mark.parametrize("price,fees,pct", [(10001, 333, 25), (999, 0, 33.3), (123457, 16543, 17.5)])
def test_split_always_adds_up(price, fees, pct):
    s = money.split(price, fees, pct, fees_before=True)
    assert s["commission_cents"] + s["consignor_cents"] == price - fees
    s = money.split(price, fees, pct, fees_before=False)
    assert s["commission_cents"] + s["consignor_cents"] == price


def test_fees_before_commission_shares_the_fee():
    # $100 sale, $13 fee, 25%: base 87 -> shop 21.75, consignor 65.25
    assert money.split(10000, 1300, 25, fees_before=True) == {"commission_cents": 2175, "consignor_cents": 6525, "pct": 25}
    assert money.split(10000, 1300, 25, fees_before=False)["consignor_cents"] == 7500


def test_fee_estimate_uses_settings():
    assert money.estimate_fees("ebay", 10000) == 1325 + 40
    db.set_setting("channel_fees", {"ebay": [10, 0]})
    assert money.estimate_fees("ebay", 10000) == 1000
    assert money.estimate_fees("instore", 0) == 0


def test_parse_tiers():
    assert money.parse_tiers("0:30, $100:25%,500=20") == [[0, 30.0], [100, 25.0], [500, 20.0]]
    with pytest.raises(ValueError):
        money.parse_tiers("lots")
    with pytest.raises(ValueError):
        money.parse_tiers("0:130")
