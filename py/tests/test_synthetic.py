"""Offline tests for synthetic aggregator programs (pseudo-referrals).

Covers the CowSwap 1003 tagging rules from
docs/cowswap-1003-double-attribution.md:

  * a wallet receiving the token FROM a program contract with positive net
    delta across the tx is tagged with the program's code;
  * forwarders (net delta <= 0 in the tx) are never tagged — this is the guard
    that keeps solvers / routers / the settlement itself out;
  * recipients that did not receive from a program contract are never tagged,
    even if net-positive in the same tx;
  * a real Referral event for the same (tx, user) beats the pseudo-referral;
  * end-to-end through legs_from_rows + compute_twa: the tag forward-fills and
    is terminated by a later real Referral (single attribution stream — the
    "DR for cowswap ends" requirement).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "py"))

from drhs import events, twa  # noqa: E402
from drhs.hypersync import LogRow  # noqa: E402
from drhs.sources import template_ab  # noqa: E402
from drhs.sources.template_ab import (  # noqa: E402
    COWSWAP, SyntheticProgram, merge_referrals, synthetic_referrals,
)

SUSDS = template_ab.SUSDS_ETH
S = "0x9008d19f58aabd9ed0d60971565aa8510560ab41"  # settlement (program contract)
A = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"  # end user, net +
R = "0xcccccccccccccccccccccccccccccccccccccccc"  # forwarder, net 0
D = "0xdddddddddddddddddddddddddddddddddddddddd"  # downstream of forwarder
E = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"  # user with real Referral
DAY = 1735689600  # 2025-01-01 00:00 UTC


def _amt(x: float) -> str:
    return "0x" + format(int(x * 10**18), "064x")


def _tr(tx: str, li: int, frm: str, to: str, amount: float, *, block=100, ts=DAY) -> LogRow:
    return LogRow(block_number=block, log_index=li, block_time=ts,
                  address=SUSDS.address, topic0=events.TRANSFER_TOPIC0,
                  topic1=events.addr_to_topic(frm), topic2=events.addr_to_topic(to),
                  topic3=None, data=_amt(amount), transaction_hash=tx)


def _ref(tx: str, li: int, owner: str, code: int, *, block=100, ts=DAY) -> LogRow:
    return LogRow(block_number=block, log_index=li, block_time=ts,
                  address=SUSDS.address, topic0=events.REFERRAL_TOPIC0,
                  topic1="0x" + format(code, "064x"), topic2=events.addr_to_topic(owner),
                  topic3=None, data="0x", transaction_hash=tx)


def test_net_positive_recipient_tagged():
    rows = [_tr("0xt1", 1, S, A, 100.0)]
    tags = synthetic_referrals(rows, (COWSWAP,))
    assert tags == {("0xt1", A): (1, 1003)}


def test_forwarder_and_downstream_not_tagged():
    # S delivers 50 to R; R forwards all 50 to D within the tx.
    rows = [_tr("0xt1", 1, S, R, 50.0), _tr("0xt1", 2, R, D, 50.0)]
    tags = synthetic_referrals(rows, (COWSWAP,))
    assert ("0xt1", R) not in tags          # net 0 -> forwarder
    assert ("0xt1", D) not in tags          # net +50 but not delivered by S
    assert tags == {}


def test_partial_forward_still_tagged():
    # S delivers 100 to A; A pays 30 away in the same tx -> net +70 -> tagged.
    rows = [_tr("0xt1", 1, S, A, 100.0), _tr("0xt1", 2, A, D, 30.0)]
    tags = synthetic_referrals(rows, (COWSWAP,))
    assert tags[("0xt1", A)] == (1, 1003)


def test_real_referral_wins_same_tx():
    tr = [_tr("0xt2", 1, S, E, 30.0)]
    rf = [_ref("0xt2", 2, E, 777)]
    real = template_ab.latest_referral_from_events(rf)
    merged = merge_referrals(real, synthetic_referrals(tr, (COWSWAP,)))
    assert merged[("0xt2", E)][1] == 777


def test_unrelated_tx_untouched():
    rows = [_tr("0xt3", 1, D, A, 10.0)]     # no program contract involved
    assert synthetic_referrals(rows, (COWSWAP,)) == {}


def test_multiple_programs_latest_delivery_wins():
    other = SyntheticProgram("other", 1004, frozenset({R}))
    # both S (1003) and R (1004) deliver to A in one tx; R's delivery is later.
    rows = [_tr("0xt4", 1, S, A, 10.0), _tr("0xt4", 2, R, A, 5.0)]
    tags = synthetic_referrals(rows, (COWSWAP, other))
    assert tags[("0xt4", A)] == (2, 1004)


def test_end_to_end_tag_starts_and_ends():
    """Day 1: CowSwap delivery -> 1003. Day 2: real Referral 777 re-tags."""
    day2 = DAY + 86400
    tr_rows = [
        _tr("0xt1", 1, S, A, 100.0, block=100, ts=DAY),
        _tr("0xt5", 1, D, A, 50.0, block=200, ts=day2),   # deposit w/ referral
    ]
    ref_rows = [_ref("0xt5", 2, A, 777, block=200, ts=day2)]
    end_ts = day2 + 86400
    legs = template_ab.legs_from_rows(SUSDS, ref_rows, tr_rows, end_ts, (COWSWAP,))
    out = twa.compute_twa(legs, fill_through=date(2025, 1, 3))
    a_rows = out[out["user_addr"] == A].sort_values("dt")
    by_day = {str(r.dt)[:10]: int(r.ref_code) for r in a_rows.itertuples()}
    assert by_day["2025-01-01"] == 1003     # tagged from the delivery
    assert by_day["2025-01-02"] == 777      # later real code ENDS 1003
    assert by_day["2025-01-03"] == 777      # ffill keeps the real code


def test_net_delta_exact_int_no_float_residue():
    """A perfect forwarder must never be tagged, even when raw wei amounts
    exceed float64's 2**53 exact-integer range (real sUSDS transfers are
    1e18-1e23 wei). With float accumulation, receive 2**53+3 then send
    2**53+1 and 2 leaves a spurious +2 residue -> false tag."""
    def _tr_wei(tx, li, frm, to, wei):
        return LogRow(block_number=100, log_index=li, block_time=DAY,
                      address=SUSDS.address, topic0=events.TRANSFER_TOPIC0,
                      topic1=events.addr_to_topic(frm), topic2=events.addr_to_topic(to),
                      topic3=None, data="0x" + format(wei, "064x"), transaction_hash=tx)
    rows = [
        _tr_wei("0xt1", 1, S, R, 2**53 + 3),   # delivery to forwarder R
        _tr_wei("0xt1", 2, R, D, 2**53 + 1),   # R forwards everything...
        _tr_wei("0xt1", 3, R, D, 2),           # ...in two hops. True net = 0.
    ]
    assert synthetic_referrals(rows, (COWSWAP,)) == {}


def test_eligibility_window():
    """Deliveries outside [start, end) are not tagged."""
    windowed = SyntheticProgram("w", 1003, frozenset({S}),
                                start=date(2025, 1, 2), end=date(2025, 1, 3))
    before = [_tr("0xt1", 1, S, A, 10.0, ts=DAY)]                # 2025-01-01
    inside = [_tr("0xt2", 1, S, A, 10.0, ts=DAY + 86400)]        # 2025-01-02
    after = [_tr("0xt3", 1, S, A, 10.0, ts=DAY + 2 * 86400)]     # 2025-01-03
    assert synthetic_referrals(before, (windowed,)) == {}
    assert synthetic_referrals(inside, (windowed,)) == {("0xt2", A): (1, 1003)}
    assert synthetic_referrals(after, (windowed,)) == {}


def test_without_synthetic_unchanged():
    """synthetic=() is byte-identical to the pre-existing path (parity guard)."""
    tr_rows = [_tr("0xt1", 1, S, A, 100.0)]
    legs = template_ab.legs_from_rows(SUSDS, [], tr_rows, DAY + 86400)
    assert legs["ref_code"].isna().all()


def _mint(tx, li, to, amount):
    return _tr(tx, li, "0x" + "0" * 40, to, amount)


def test_mint_path_canary_warns(caplog):
    """A net-positive mint recipient inside a delivery tx that we did NOT tag
    is the mint-path gap — it must show up in logs, never silently."""
    rows = [_tr("0xt1", 1, S, A, 100.0),    # normal delivery (tagged)
            _mint("0xt1", 2, E, 30.0)]      # solver mints straight to E
    with caplog.at_level("WARNING"):
        tags = synthetic_referrals(rows, (COWSWAP,))
    assert tags == {("0xt1", A): (1, 1003)}          # E stays untagged (the gap)...
    assert any("mint-path gap" in m for m in caplog.messages)  # ...but loudly


def test_mint_to_forwarder_no_warning(caplog):
    """The normal solver flow (mint to intermediary, settlement delivers) must
    not trigger the canary: the mint recipient nets to zero."""
    rows = [_mint("0xt1", 1, R, 50.0),      # solver R receives the mint
            _tr("0xt1", 2, R, S, 50.0),     # hands it to the settlement
            _tr("0xt1", 3, S, A, 50.0)]     # settlement delivers to the user
    with caplog.at_level("WARNING"):
        tags = synthetic_referrals(rows, (COWSWAP,))
    assert tags == {("0xt1", A): (3, 1003)}
    assert not any("mint-path gap" in m for m in caplog.messages)


def test_excluded_contract_tag_produces_no_legs():
    """A pooled contract (TEMPLATE_A_EXCLUDED) tagged by a delivery must still
    be dropped from the legs — the exclusion guard wins end-to-end."""
    morpho = "0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb"
    assert morpho in template_ab.TEMPLATE_A_EXCLUDED
    tr_rows = [_tr("0xt1", 1, S, morpho, 100.0)]
    raw = template_ab.legs_from_rows(SUSDS, [], tr_rows, DAY + 86400, (COWSWAP,))
    # the tag exists at the referral layer...
    assert synthetic_referrals(tr_rows, (COWSWAP,))[("0xt1", morpho)] == (1, 1003)
    # ...but the excluded filter (applied in build_legs) removes the user's legs
    filtered = raw[~raw["user_addr"].str.lower().isin(template_ab.TEMPLATE_A_EXCLUDED)]
    assert not (filtered["user_addr"] == morpho).any()
