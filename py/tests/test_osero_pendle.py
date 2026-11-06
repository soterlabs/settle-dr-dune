"""Conservative Phase-1 Osero/Pendle attribution and Skybase carve-out."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "py"))

from drhs import events  # noqa: E402
from drhs.hypersync import LogRow  # noqa: E402
from drhs.sources import custody, osero_pendle as op, skybase  # noqa: E402


S = sorted(op.STRATEGIES)[0]
ZERO = events.ZERO_ADDR
TX_TS = 1_790_000_000  # before expiry


def _data(*words: int) -> str:
    return "0x" + "".join(f"{w:064x}" for w in words)


def _row(addr, topic0, *, tx, idx, topics=(), data=(), block=100, ts=TX_TS):
    padded = list(topics) + [None] * (3 - len(topics))
    return LogRow(block, idx, ts, addr, topic0, padded[0], padded[1], padded[2],
                  _data(*data), tx)


def _tr(token, frm, to, amount, *, tx, idx, block=100, ts=TX_TS):
    return _row(token, events.TRANSFER_TOPIC0, tx=tx, idx=idx,
                topics=(events.addr_to_topic(frm), events.addr_to_topic(to)),
                data=(amount,), block=block, ts=ts)


def _tagged_wrap(amount=100 * 10**18, code=3017):
    tx = "0xtag"
    return [
        _row(op.SUSDS, events.DEPOSIT_TOPIC0, tx=tx, idx=1,
             topics=(events.addr_to_topic(S), events.addr_to_topic(S)),
             data=(amount, amount)),
        _tr(op.SUSDS, ZERO, S, amount, tx=tx, idx=2),
        _row(op.SUSDS, events.REFERRAL_TOPIC0, tx=tx, idx=3,
             topics=(f"0x{code:064x}", events.addr_to_topic(S)),
             data=(amount, amount)),
        _tr(op.SUSDS, S, op.SY, amount, tx=tx, idx=4),
        _tr(op.SY, ZERO, S, amount, tx=tx, idx=5),
        _row(op.SY, op.SY_DEPOSIT, tx=tx, idx=6,
             topics=(events.addr_to_topic(S), events.addr_to_topic(S),
                     events.addr_to_topic(op.SUSDS)), data=(amount, amount)),
    ]


def _split(amount=100 * 10**18, py=110 * 10**18):
    tx = "0xsplit"
    return [
        _tr(op.SY, S, op.YT, amount, tx=tx, idx=1, block=101),
        _tr(op.YT, ZERO, S, py, tx=tx, idx=2, block=101),
        _tr(op.PT, ZERO, S, py, tx=tx, idx=3, block=101),
        _row(op.YT, op.YT_MINT, tx=tx, idx=4, block=101,
             topics=(events.addr_to_topic(S), events.addr_to_topic(S),
                     events.addr_to_topic(S)), data=(amount, py)),
    ]


def test_tagged_wrap_split_and_named_morpho_supply_preserve_credit():
    amount, py = 100 * 10**18, 110 * 10**18
    tx = "0xsupply"
    supply = [
        _tr(op.PT, S, custody.MORPHO_BLUE, py, tx=tx, idx=1, block=102),
        _row(custody.MORPHO_BLUE, custody.SUPPLY_COLLATERAL_TOPIC0,
             tx=tx, idx=2, block=102,
             topics=(next(iter(op.MORPHO_MARKETS)), events.addr_to_topic(S),
                     events.addr_to_topic(S)), data=(py,)),
    ]
    legs = op.replay(_tagged_wrap(amount) + _split(amount, py) + supply, TX_TS + 100)
    assert list(legs.ref_code) == [3017]
    assert legs.amount_change.sum() == pytest.approx(100)


def test_pt_or_yt_sale_consumes_matched_credit_and_inflow_does_not_restore_it():
    rows = _tagged_wrap() + _split()
    rows += [_tr(op.PT, S, "0x" + "a" * 40, 44 * 10**18,
                 tx="0xsale", idx=1, block=102)]
    rows += [_tr(op.PT, "0x" + "b" * 40, S, 44 * 10**18,
                 tx="0xin", idx=1, block=103)]
    legs = op.replay(rows, TX_TS + 100)
    # 44/110 of the 100 referred shares were disposed; the later inflow is untagged.
    assert legs.amount_change.sum() == pytest.approx(60)


def test_selling_both_sides_separately_consumes_each_matched_tranche_once():
    rows = _tagged_wrap() + _split()
    rows += [_tr(op.PT, S, "0x" + "a" * 40, 44 * 10**18,
                 tx="0xptsale", idx=1, block=102)]
    rows += [_tr(op.YT, S, "0x" + "b" * 40, 44 * 10**18,
                 tx="0xytsale", idx=1, block=103)]
    legs = op.replay(rows, TX_TS + 100)
    # The second sale disposes the counterpart to the already-lost tranche;
    # controlled matched PT/YT remains 66/110, i.e. 60 referred shares.
    assert legs.amount_change.sum() == pytest.approx(60)


def test_expiry_switches_from_matched_pair_to_pt_only_without_later_tx():
    rows = _tagged_wrap() + _split()
    rows += [_tr(op.YT, S, "0x" + "a" * 40, 44 * 10**18,
                 tx="0xytsale", idx=1, block=102)]
    legs = op.replay(rows, op.EXPIRY_TS + 1, expiry_block=200)
    expiry = legs[legs.ts == op.EXPIRY_TS]
    assert len(expiry) == 1
    assert expiry.iloc[0].block == 200
    # Before expiry the missing YT reduces eligibility to 60 shares. At
    # maturity all 110 controlled PT units represent the full 100-share claim.
    assert expiry.iloc[0].amount_change == pytest.approx(40)
    assert legs.amount_change.sum() == pytest.approx(100)


def test_morpho_withdrawal_to_strategy_is_custody_but_external_receiver_is_disposal():
    rows = _tagged_wrap() + _split()
    market = next(iter(op.MORPHO_MARKETS))
    inside = "0xinside"
    rows += [
        _row(custody.MORPHO_BLUE, custody.WITHDRAW_COLLATERAL_TOPIC0,
             tx=inside, idx=1, block=102,
             topics=(market, events.addr_to_topic(S), events.addr_to_topic(S)),
             data=(int(S, 16), 11 * 10**18)),
        _tr(op.PT, custody.MORPHO_BLUE, S, 11 * 10**18,
            tx=inside, idx=2, block=102),
    ]
    outside = "0x" + "c" * 40
    rows += [
        _row(custody.MORPHO_BLUE, custody.WITHDRAW_COLLATERAL_TOPIC0,
             tx="0xoutside", idx=1, block=103,
             topics=(market, events.addr_to_topic(S), events.addr_to_topic(outside)),
             data=(int(S, 16), 11 * 10**18)),
        _tr(op.PT, custody.MORPHO_BLUE, outside, 11 * 10**18,
            tx="0xoutside", idx=2, block=103),
    ]
    legs = op.replay(rows, TX_TS + 100)
    assert legs.amount_change.sum() == pytest.approx(90)


def test_non_osero_or_non_allowlisted_referral_creates_no_credit():
    assert op.replay(_tagged_wrap(code=2001), TX_TS + 100).empty
    # Numeric range is not beneficiary ownership: 3000 is a known real Osero
    # code elsewhere, but this Phase-1 proposal is explicitly code 3017.
    assert op.replay(_tagged_wrap(code=3000), TX_TS + 100).empty
    other = "0x" + "d" * 40
    rows = _tagged_wrap()
    for r in rows:
        if r.topic1 and events.topic_to_addr(r.topic1) == S:
            object.__setattr__(r, "topic1", events.addr_to_topic(other))
        if r.topic2 and events.topic_to_addr(r.topic2) == S:
            object.__setattr__(r, "topic2", events.addr_to_topic(other))
        if r.topic3 and events.topic_to_addr(r.topic3) == S:
            object.__setattr__(r, "topic3", events.addr_to_topic(other))
    assert op.replay(rows, TX_TS + 100).empty


def test_skybase_deduction_is_exact_inverse():
    legs = op.replay(_tagged_wrap(), TX_TS + 100)
    deductions = op.skybase_deductions(legs)
    assert deductions.amount_change.sum() == pytest.approx(-100)
    assert set(deductions.user_addr) == {op.SY}
    assert set(deductions.ref_code) == {1997}
    assert pd.concat([legs, deductions]).amount_change.sum() == pytest.approx(0)


def test_skybase_split_preserves_total_backing_and_has_nonnegative_residual():
    osero = op.replay(_tagged_wrap(), TX_TS + 100)
    base = osero.copy()
    base["user_addr"] = op.SY
    base["ref_code"] = 1997
    combined = skybase.compose_pendle_attribution(base, osero)
    assert combined.amount_change.sum() == pytest.approx(base.amount_change.sum())
    residual = combined[(combined.user_addr == op.SY) & (combined.ref_code == 1997)]
    assert residual.amount_change.sum() == pytest.approx(0)


def test_skybase_split_rejects_more_osero_credit_than_sy_backing():
    osero = op.replay(_tagged_wrap(), TX_TS + 100)
    base = osero.copy()
    base["amount_change"] = 99.0
    base["user_addr"] = op.SY
    base["ref_code"] = 1997
    with pytest.raises(ValueError, match="exceeds Skybase SY backing"):
        skybase.compose_pendle_attribution(base, osero)
