from kitchen_prep.pipeline import fefo


BEEF = [
    {"batch_id": "b01", "item_id": "beef_patty", "qty": 22, "expiry_date": "2026-08-14"},
    {"batch_id": "b02", "item_id": "beef_patty", "qty": 18, "expiry_date": "2026-08-16"},
]


def test_split_expired_flags_only_past():
    batches = BEEF + [
        {"batch_id": "b06", "item_id": "pork_ribs", "qty": 7, "expiry_date": "2026-08-13"}
    ]
    valid, waste = fefo.split_expired(batches, "2026-08-14")
    assert [b["batch_id"] for b in waste] == ["b06"]
    assert {b["batch_id"] for b in valid} == {"b01", "b02"}


def test_same_day_expiry_is_valid():
    valid, waste = fefo.split_expired(BEEF, "2026-08-14")
    assert not waste
    assert len(valid) == 2


def test_consume_uses_earliest_expiry_first():
    consumption, uncovered, remaining = fefo.consume(30, BEEF)
    assert uncovered == 0
    # b01 (earliest expiry) consumed first and fully, then b02.
    assert consumption[0]["batch_id"] == "b01"
    assert consumption[0]["qty_consumed"] == 22
    assert consumption[1]["batch_id"] == "b02"
    assert consumption[1]["qty_consumed"] == 8
    rem = {b["batch_id"]: b["qty"] for b in remaining}
    assert rem == {"b02": 10}


def test_consume_reports_uncovered():
    consumption, uncovered, remaining = fefo.consume(100, BEEF)
    assert uncovered == 60
    assert remaining == []
