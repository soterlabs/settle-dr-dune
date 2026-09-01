"""Offline tests for custody perimeters (drhs.sources.custody).

Covers the W+C-by-construction model for the Osero gtSkyLooping case
(docs/osero-looping-vault.md): supplies are relocations, unmatched transfers
to Morpho stay disposals, withdrawals/liquidations resolve per the spec, and
the 3009 tag rides the ordinary (tx, owner) referral attachment.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "py"))

from drhs import events, twa  # noqa: E402
from drhs.hypersync import LogRow  # noqa: E402
from drhs.sources import custody, template_ab  # noqa: E402

T = template_ab.SUSDS_ETH
P = custody.OSERO_GTSKYLOOPING
STRAT = P.strategy
MORPHO = custody.MORPHO_BLUE
MKT = "0x" + "ab" * 32
OTHER = "0x1111111111111111111111111111111111111111"
DAY = 1735689600  # 2025-01-01 UTC
END = DAY + 3 * 86400


def _pad(a: str) -> str:
    return events.addr_to_topic(a)


def _u256(x: float) -> str:
    return format(int(x * 10**18), "064x")


def _tr(tx, li, frm, to, amount, ts=DAY, block=100):
    return LogRow(block_number=block, log_index=li, block_time=ts,
                  address=T.address, topic0=events.TRANSFER_TOPIC0,
                  topic1=_pad(frm), topic2=_pad(to), topic3=None,
                  data="0x" + _u256(amount), transaction_hash=tx)


def _ref(tx, li, owner, code, ts=DAY, block=100):
    return LogRow(block_number=block, log_index=li, block_time=ts,
                  address=T.address, topic0=template_ab.events.REFERRAL_TOPIC0,
                  topic1="0x" + format(code, "064x"), topic2=_pad(owner),
                  topic3=None, data="0x", transaction_hash=tx)


def _sup(tx, li, caller, on_behalf, assets, ts=DAY, block=100):
    return LogRow(block_number=block, log_index=li, block_time=ts,
                  address=MORPHO, topic0=custody.SUPPLY_COLLATERAL_TOPIC0,
                  topic1=MKT, topic2=_pad(caller), topic3=_pad(on_behalf),
                  data="0x" + _u256(assets), transaction_hash=tx)


def _wdr(tx, li, caller, on_behalf, receiver, assets, ts=DAY, block=100):
    return LogRow(block_number=block, log_index=li, block_time=ts,
                  address=MORPHO, topic0=custody.WITHDRAW_COLLATERAL_TOPIC0,
                  topic1=MKT, topic2=_pad(on_behalf), topic3=_pad(receiver),
                  data="0x" + _pad(caller)[2:] + _u256(assets), transaction_hash=tx)


def _liq(tx, li, caller, borrower, seized, ts=DAY, block=100):
    words = _u256(0) + _u256(0) + _u256(seized) + _u256(0) + _u256(0)
    return LogRow(block_number=block, log_index=li, block_time=ts,
                  address=MORPHO, topic0=custody.LIQUIDATE_TOPIC0,
                  topic1=MKT, topic2=_pad(caller), topic3=_pad(borrower),
                  data="0x" + words, transaction_hash=tx)


def _balance(tr_rows, ref_rows, morpho_rows):
    legs = template_ab.legs_from_rows(T, ref_rows, tr_rows, END,
                                      custody_rows=[(P, morpho_rows)])
    if legs.empty:
        return 0.0, legs
    mine = legs[legs["user_addr"] == STRAT]
    return float(mine["amount_change"].sum()), legs


def test_supply_is_relocation_and_keeps_tag():
    """Mint 100 w/ Referral(3009) then supply to Morpho: balance stays 100,
    tagged 3009 (the C-leg picks the code up from the same-tx Referral)."""
    tr = [_tr("0xt1", 1, "0x" + "0" * 40, STRAT, 100.0),
          _tr("0xt1", 3, STRAT, MORPHO, 100.0)]
    rf = [_ref("0xt1", 2, STRAT, 3009)]
    mo = [_sup("0xt1", 4, STRAT, STRAT, 100.0)]
    bal, legs = _balance(tr, rf, mo)
    assert bal == 100.0
    out = twa.compute_twa(legs[legs["user_addr"] == STRAT],
                          fill_through=date(2025, 1, 2))
    assert set(out["ref_code"]) == {3009}
    assert out[out["dt"].astype(str) == "2025-01-02"]["time_weighted_avg_balance"].iloc[0] == 100.0


def test_unmatched_transfer_to_morpho_is_disposal():
    """Spec §6.4: a transfer to Morpho with no position event stays a disposal."""
    tr = [_tr("0xt1", 1, "0x" + "0" * 40, STRAT, 100.0),
          _tr("0xt2", 1, STRAT, MORPHO, 100.0)]
    bal, _ = _balance(tr, [], [])
    assert bal == 0.0


def test_withdraw_to_strategy_is_relocation():
    tr = [_tr("0xt1", 1, "0x" + "0" * 40, STRAT, 100.0),
          _tr("0xt1", 2, STRAT, MORPHO, 100.0),
          _tr("0xt2", 1, MORPHO, STRAT, 40.0, ts=DAY + 60, block=101)]
    mo = [_sup("0xt1", 3, STRAT, STRAT, 100.0),
          _wdr("0xt2", 2, STRAT, STRAT, STRAT, 40.0, ts=DAY + 60, block=101)]
    bal, _ = _balance(tr, [], mo)
    assert bal == 100.0  # 60 in Morpho + 40 back in the wallet


def test_withdraw_to_other_is_disposal():
    tr = [_tr("0xt1", 1, "0x" + "0" * 40, STRAT, 100.0),
          _tr("0xt1", 2, STRAT, MORPHO, 100.0)]
    mo = [_sup("0xt1", 3, STRAT, STRAT, 100.0),
          _wdr("0xt2", 1, STRAT, STRAT, OTHER, 30.0, ts=DAY + 60, block=101)]
    bal, _ = _balance(tr, [], mo)
    assert bal == 70.0


def test_external_liquidation_is_disposal():
    tr = [_tr("0xt1", 1, "0x" + "0" * 40, STRAT, 100.0),
          _tr("0xt1", 2, STRAT, MORPHO, 100.0)]
    mo = [_sup("0xt1", 3, STRAT, STRAT, 100.0),
          _liq("0xt2", 1, OTHER, STRAT, 25.0, ts=DAY + 60, block=101)]
    bal, _ = _balance(tr, [], mo)
    assert bal == 75.0


def test_self_liquidation_is_relocation():
    """Seized collateral returning to the strategy's own wallet nets to zero."""
    tr = [_tr("0xt1", 1, "0x" + "0" * 40, STRAT, 100.0),
          _tr("0xt1", 2, STRAT, MORPHO, 100.0),
          _tr("0xt2", 2, MORPHO, STRAT, 25.0, ts=DAY + 60, block=101)]
    mo = [_sup("0xt1", 3, STRAT, STRAT, 100.0),
          _liq("0xt2", 1, STRAT, STRAT, 25.0, ts=DAY + 60, block=101)]
    bal, _ = _balance(tr, [], mo)
    assert bal == 100.0


def test_third_party_supply_credits_and_warns(caplog):
    mo = [_sup("0xt1", 1, OTHER, STRAT, 50.0)]
    with caplog.at_level("WARNING"):
        bal, _ = _balance([], [], mo)
    assert bal == 50.0
    assert any("third-party SupplyCollateral" in m for m in caplog.messages)


def test_window_filter_applies_to_custody_legs():
    mo = [_sup("0xt1", 1, STRAT, STRAT, 50.0, ts=END + 10, block=999)]
    bal, _ = _balance([], [], mo)
    assert bal == 0.0


def test_without_custody_unchanged():
    """No custody_rows -> byte-identical to the pre-existing path."""
    tr = [_tr("0xt1", 1, "0x" + "0" * 40, STRAT, 100.0),
          _tr("0xt1", 2, STRAT, MORPHO, 100.0)]
    legs = template_ab.legs_from_rows(T, [], tr, END)
    assert float(legs[legs["user_addr"] == STRAT]["amount_change"].sum()) == 0.0
