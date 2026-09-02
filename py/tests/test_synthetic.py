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
    COWSWAP, REROUTED_CODES, SyntheticProgram, merge_referrals,
    rerouted_referrals, synthetic_referrals,
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


# --- re-routed intermediary codes (Paraswap 1004 / 1inch 4011 shape) ----------

def _warm(owner, code, n=2):
    """Extra Referral events in unrelated txs so `owner` clears the
    MIN_INTERMEDIARY_EVENTS threshold (routers emit the code repeatedly)."""
    return [_ref(f"0xwarm{i}", 1, owner, code) for i in range(n)]


def test_reroute_forwarder_code_to_recipient():
    """Referral(1004) lands on router R (net 0); R delivers to A -> A gets 1004."""
    tr = [_tr("0xt1", 2, R, A, 100.0)]              # router forwards to user
    rf = [_ref("0xt1", 1, R, 1004)] + _warm(R, 1004)
    tags = rerouted_referrals(rf, tr, REROUTED_CODES)
    assert tags == {("0xt1", A): (2, 1004)}


def test_single_event_owner_not_treated_as_intermediary():
    """An END USER can own an allowlisted code (aggregator passed
    receiver=user). If they forward in the same tx, re-routing must NOT fire
    onto their transfer recipient — one-off owners are not intermediaries."""
    tr = [_tr("0xt1", 2, E, D, 100.0)]              # user E forwards to pool D
    rf = [_ref("0xt1", 1, E, 4011)]                 # E's one and only 4011 event
    assert rerouted_referrals(rf, tr, REROUTED_CODES) == {}
    # same shape clears the threshold when the owner is a real router
    tags = rerouted_referrals(rf + _warm(E, 4011), tr, REROUTED_CODES)
    assert tags == {("0xt1", D): (2, 4011)}


def test_vault_shape_not_rerouted():
    """Owner that RETAINS the balance (net > 0) keeps its own attribution —
    the Yearn-vault shape must never be re-routed."""
    tr = [_tr("0xt1", 2, D, R, 100.0),              # vault R receives and keeps
          _tr("0xt1", 3, R, A, 10.0)]               # small payout to A
    rf = [_ref("0xt1", 1, R, 1004)] + _warm(R, 1004)
    assert rerouted_referrals(rf, tr, REROUTED_CODES) == {}


def test_reroute_only_allowlisted_codes():
    """Non-allowlisted intermediary codes are left exactly as they are."""
    tr = [_tr("0xt1", 2, R, A, 100.0)]
    rf = [_ref("0xt1", 1, R, 1007)]                 # yearn: not in the allowlist
    assert rerouted_referrals(rf, tr, REROUTED_CODES) == {}


def test_reroute_skips_forwarding_recipient():
    """Recipient that itself forwards on (net 0) is a hop, not the end user."""
    tr = [_tr("0xt1", 2, R, D, 100.0),              # router -> hop D
          _tr("0xt1", 3, D, A, 100.0)]              # hop D -> user A
    rf = [_ref("0xt1", 1, R, 1004)] + _warm(R, 1004)
    tags = rerouted_referrals(rf, tr, REROUTED_CODES)
    assert ("0xt1", D) not in tags                  # net 0 -> not tagged
    assert tags == {}                               # A didn't receive FROM the owner


def test_reroute_real_user_referral_wins_and_beats_pseudo():
    """Precedence: real user Referral > re-routed code > delivery pseudo-tag."""
    # tx1: user E has their OWN referral -> re-routed 1004 must not override it.
    tr1 = [_tr("0xt1", 2, R, E, 50.0)]
    rf1 = [_ref("0xt1", 1, R, 1004), _ref("0xt1", 3, E, 777)] + _warm(R, 1004)
    legs = template_ab.legs_from_rows(SUSDS, rf1, tr1, DAY + 86400,
                                      (COWSWAP,), REROUTED_CODES)
    e_ref = legs[legs["user_addr"] == E]["ref_code"].dropna().unique()
    assert list(e_ref) == [777]
    # tx2: cowswap delivery AND a re-routed 1004 for the same user -> 1004 wins
    # (re-route carries explicit on-chain code evidence).
    tr2 = [_tr("0xt2", 2, S, A, 30.0),              # cowswap delivery -> pseudo 1003
           _tr("0xt2", 3, R, A, 20.0)]              # paraswap router delivery
    rf2 = [_ref("0xt2", 1, R, 1004)] + _warm(R, 1004)
    legs2 = template_ab.legs_from_rows(SUSDS, rf2, tr2, DAY + 86400,
                                       (COWSWAP,), REROUTED_CODES)
    a_ref = legs2[legs2["user_addr"] == A]["ref_code"].dropna().unique()
    assert list(a_ref) == [1004]


def test_reroute_end_to_end_terminates_cowswap_tag():
    """The reported scenario: CowSwap buy (1003), later Paraswap buy -> the
    re-routed 1004 ENDS the 1003 tag instead of double counting."""
    day2 = DAY + 86400
    tr = [_tr("0xt1", 1, S, A, 100.0, block=100, ts=DAY),        # cowswap
          _tr("0xt5", 2, R, A, 50.0, block=200, ts=day2)]        # paraswap
    rf = [_ref("0xt5", 1, R, 1004, block=200, ts=day2)] + _warm(R, 1004)
    legs = template_ab.legs_from_rows(SUSDS, rf, tr, day2 + 86400,
                                      (COWSWAP,), REROUTED_CODES)
    out = twa.compute_twa(legs, fill_through=date(2025, 1, 3))
    by_day = {str(r.dt)[:10]: int(r.ref_code)
              for r in out[out["user_addr"] == A].itertuples()}
    assert by_day["2025-01-01"] == 1003
    assert by_day["2025-01-02"] == 1004     # paraswap ends the cowswap tag
    assert by_day["2025-01-03"] == 1004


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


# --- Osero 3006 via Jumper Earn (Li.Fi): two-hop re-route ---------------------
# On-chain shape (docs/osero-codes.md): Jumper's deposit adapter J mints the
# shares with Referral(3006) on itself, hands them to the LiFiDiamond L (net 0),
# and L delivers to the user. Neither J nor L keeps anything.
J = "0xe69b860fb5f12552b9c7675966ef9522fb734232"   # Jumper Earn deposit adapter
L = "0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae"   # LiFiDiamond
SUSDC_BASE = template_ab.SUSDC_BASE


def _jumper_tx(tx, user, amount, *, block=100, ts=DAY, code=3006):
    tr = [_tr(tx, 1, "0x" + "0" * 40, J, amount, block=block, ts=ts),   # vault mints to the adapter
          _tr(tx, 3, J, L, amount, block=block, ts=ts),
          _tr(tx, 4, L, user, amount, block=block, ts=ts)]
    rf = [_ref(tx, 2, J, code, block=block, ts=ts)]
    return tr, rf


def test_3006_is_rerouted_and_follows_hops():
    assert 3006 in REROUTED_CODES
    assert 3006 in template_ab.REROUTE_FOLLOW_HOPS
    assert 1004 not in template_ab.REROUTE_FOLLOW_HOPS   # settled rule untouched
    assert 4011 not in template_ab.REROUTE_FOLLOW_HOPS


def test_jumper_two_hop_reroute_tags_end_user_only():
    tr, rf = _jumper_tx("0xt1", A, 1899.94)
    tags = rerouted_referrals(rf + _warm(J, 3006), tr, REROUTED_CODES)
    assert tags == {("0xt1", A): (4, 3006)}          # final delivery edge's log_index
    assert ("0xt1", L) not in tags and ("0xt1", J) not in tags


def test_two_hop_not_followed_for_direct_rule_codes():
    """Same graph under 1004 (not in REROUTE_FOLLOW_HOPS): the hop stops it,
    exactly as before — settled Paraswap/1inch attribution is byte-identical."""
    tr, rf = _jumper_tx("0xt1", A, 100.0, code=1004)
    assert rerouted_referrals(rf + _warm(J, 1004), tr, REROUTED_CODES) == {}


def test_hop_following_stops_at_retaining_contract():
    """A net-positive recipient along the chain is an END recipient (tagged),
    never a hop — the walk must not continue through a contract that keeps
    the balance (pooled-holder relabel is the accepted, explicit outcome)."""
    tr = [_tr("0xt1", 1, "0x" + "0" * 40, J, 100.0),
          _tr("0xt1", 3, J, L, 100.0),
          _tr("0xt1", 4, L, D, 100.0),              # D keeps 70, pays 30 on to A
          _tr("0xt1", 5, D, A, 30.0)]
    rf = [_ref("0xt1", 2, J, 3006)] + _warm(J, 3006)
    tags = rerouted_referrals(rf, tr, REROUTED_CODES)
    assert tags == {("0xt1", D): (4, 3006)}          # A is not reached through D


def test_hop_following_cycle_safe():
    """A forwarder that bounces the token back must not loop the walk."""
    tr = [_tr("0xt1", 1, "0x" + "0" * 40, J, 10.0),
          _tr("0xt1", 3, J, L, 10.0), _tr("0xt1", 4, L, J, 10.0),   # bounce
          _tr("0xt1", 5, J, L, 10.0), _tr("0xt1", 6, L, A, 10.0)]
    rf = [_ref("0xt1", 2, J, 3006)] + _warm(J, 3006)
    assert rerouted_referrals(rf, tr, REROUTED_CODES) == {("0xt1", A): (6, 3006)}


def test_jumper_end_to_end_on_susdc_target():
    """Through legs_from_rows on an sUSDC L2 target: the user's balance carries
    3006 from the deposit day on; adapter and Diamond produce zero-balance legs
    only (they net to 0 in the tx)."""
    tr, rf = _jumper_tx("0xt1", A, 500.0)
    legs = template_ab.legs_from_rows(SUSDC_BASE, rf + _warm(J, 3006), tr, DAY + 2 * 86400,
                                      (), REROUTED_CODES)
    out = twa.compute_twa(legs, fill_through=date(2025, 1, 2))
    a = out[out["user_addr"] == A]
    assert set(a["ref_code"].astype(int)) == {3006}
    assert abs(a["time_weighted_avg_balance"].iloc[-1] - 500.0) < 1e-9
    assert not (out["user_addr"].isin([J, L]) & (out["time_weighted_avg_balance"] > 0)).any()


def test_susdc_sources_carry_reroute():
    from run_source import SPECS
    for name in ("susds_eth", "susdc", "susdc_mar", "susdc_jun"):
        assert 3006 in SPECS[name].reroute, name


# --- entrypoint-anchored programs (1inch → Skybase 1020) -------------------------
from drhs.sources.template_ab import EntrypointProgram, ONEINCH_SKYBASE  # noqa: E402

V6 = "0x111111125421ca6dc452d289314280a0f8842a65"
EXEC = "0x5141b82f5ffda4c6fe1e372978f1c5427640a190"   # a 1inch solver executor
ONEINCH_OPEN = EntrypointProgram("t", 1020, ONEINCH_SKYBASE.entrypoints)  # no window


def _tr_to(tx, li, frm, to, amount, tx_to, **kw):
    r = _tr(tx, li, frm, to, amount, **kw)
    return LogRow(**{**r.__dict__, "tx_to": tx_to})


def test_entrypoint_resolves_from_tx_to():
    rows = [_tr_to("0xr", 1, EXEC, A, 10.0, V6), _tr_to("0xo", 1, D, A, 10.0, D)]
    prog = ONEINCH_OPEN.resolve_from_rows(rows)
    assert isinstance(prog, SyntheticProgram)
    assert prog.txs == {"0xr"} and prog.contracts == frozenset() and prog.ref_code == 1020


def test_entrypoint_tags_any_sender_and_mints_in_anchored_txs():
    rows = [_tr_to("0xr1", 1, EXEC, A, 10.0, V6),                 # executor delivers
            _tr_to("0xr2", 1, "0x" + "0" * 40, E, 5.0, V6),      # vault mints straight to user
            _tr_to("0xr3", 1, V6, D, 3.0, V6),                   # router delivers directly
            _tr_to("0xo", 1, EXEC, R, 10.0, D)]                  # same executor, other entrypoint
    prog = ONEINCH_OPEN.resolve_from_rows(rows)
    tags = synthetic_referrals(rows, (prog,))
    assert tags == {("0xr1", A): (1, 1020), ("0xr2", E): (1, 1020), ("0xr3", D): (1, 1020)}


def test_entrypoint_forwarders_not_tagged():
    rows = [_tr_to("0xr", 1, "0x" + "0" * 40, EXEC, 10.0, V6),   # mint to executor (net 0)
            _tr_to("0xr", 2, EXEC, V6, 10.0, V6),                 # router hop (net 0)
            _tr_to("0xr", 3, V6, A, 10.0, V6)]                    # user (net +)
    prog = ONEINCH_OPEN.resolve_from_rows(rows)
    assert synthetic_referrals(rows, (prog,)) == {("0xr", A): (3, 1020)}


def test_entrypoint_window_and_precedence_with_4011_reroute():
    """Inside the window the executor's real Referral(4011), re-routed, beats
    the 1020 pseudo-tag (settled precedence); a plain 1inch tx gets 1020."""
    rows = [_tr_to("0xa", 2, EXEC, A, 10.0, V6), _tr_to("0xb", 2, EXEC, D, 10.0, V6)]
    rf = [_ref("0xa", 1, EXEC, 4011)] + _warm(EXEC, 4011)
    prog = ONEINCH_OPEN.resolve_from_rows(rows)
    legs = template_ab.legs_from_rows(SUSDS, rf, rows, DAY + 86400, (prog,), REROUTED_CODES)
    code = lambda w: set(legs[legs["user_addr"] == w]["ref_code"].dropna().astype(int))
    assert code(A) == {4011} and code(D) == {1020}
    # outside the eligibility window nothing is tagged
    windowed = EntrypointProgram("w", 1020, ONEINCH_SKYBASE.entrypoints, start=date(2025, 1, 2))
    assert synthetic_referrals(rows, (windowed.resolve_from_rows(rows),)) == {}


def test_entrypoint_program_wired_and_provisional_start():
    from run_source import SPECS
    for src in ("susds_eth", "susdc", "susdc_mar", "susdc_jun"):
        names = [getattr(p, "name", None) for p in SPECS[src].synthetic]
        assert "oneinch_skybase" in names, src
    assert ONEINCH_SKYBASE.start == date(2026, 9, 1)     # no settled month re-attributed by default
    assert len(ONEINCH_SKYBASE.entrypoints) == 3


def test_logrow_tx_to_defaults_none_for_fixtures():
    r = _tr("0xt", 1, S, A, 1.0)
    assert r.tx_to is None
    assert ONEINCH_OPEN.resolve_from_rows([r]).txs == frozenset()


# --- review fixes: tie rule, temporal hop guard, bounded entrypoint fetch --------

def test_shared_contract_tie_last_program_in_tuple_wins():
    """Two programs anchored on the same contract AND the same tx: the last
    program in the tuple wins (the pre-multi-program by_contract semantics)."""
    a = SyntheticProgram("a", 3900, frozenset({L}), txs=frozenset({"0x1"}))
    b = SyntheticProgram("b", 3901, frozenset({L}), txs=frozenset({"0x1"}))
    rows = [_tr("0x1", 1, L, A, 1.0)]
    assert synthetic_referrals(rows, (a, b)) == {("0x1", A): (1, 3901)}
    assert synthetic_referrals(rows, (b, a)) == {("0x1", A): (1, 3900)}


def test_hop_following_ignores_edges_before_the_hop_was_funded():
    """Shared router L: an unrelated earlier delivery L->U1 (log 2) must not
    inherit 3006 when J funds L later (log 8) and L delivers to U2 (log 9)."""
    U1, U2 = A, D
    rows = [_tr("0xt", 1, R, L, 5.0), _tr("0xt", 2, L, U1, 5.0),          # unrelated leg, earlier
            _tr("0xt", 7, "0x" + "0" * 40, J, 10.0), _tr("0xt", 8, J, L, 10.0),
            _tr("0xt", 9, L, U2, 10.0)]
    rf = [_ref("0xt", 7, J, 3006)] + _warm(J, 3006)
    tags = rerouted_referrals(rf, rows, REROUTED_CODES)
    assert tags == {("0xt", U2): (9, 3006)}


def test_entrypoint_resolve_fetches_bounded_joined_window(monkeypatch):
    """resolve() fetches its own Transfer rows WITH the join, from the program's
    start — never re-keying the pipeline's full Transfer stream."""
    from drhs import hypersync as hs
    calls = []
    def fake_query(chain, selections, fb, tb, **kw):
        calls.append((fb, tb, kw.get("with_tx_to")))
        return hs.QueryResult(rows=[_tr_to("0xr", 1, EXEC, A, 1.0, V6)])
    monkeypatch.setattr(hs, "query_logs", fake_query)
    monkeypatch.setattr(hs, "find_block_at_or_before", lambda chain, ts: 5_000)   # block at program.start
    prog = EntrypointProgram("t", 1020, ONEINCH_SKYBASE.entrypoints, start=date(2026, 7, 1))
    end_ts = 1_785_542_400   # 2026-08-01 00:00 UTC — the window [start, end) is non-empty
    resolved = prog.resolve(SUSDS, 0, 9_000, end_ts)
    assert calls == [(5_000, 9_000, True)]          # bounded below by start, joined
    assert resolved.txs == {"0xr"} and resolved.contracts == frozenset()


def test_entrypoint_resolve_skips_query_when_window_is_after_scan_end(monkeypatch):
    from drhs import hypersync as hs
    def boom(*a, **k): raise AssertionError("must not query")
    monkeypatch.setattr(hs, "query_logs", boom); monkeypatch.setattr(hs, "find_block_at_or_before", boom)
    prog = EntrypointProgram("t", 1020, ONEINCH_SKYBASE.entrypoints, start=date(2026, 9, 1))
    end_ts_aug = 1_788_220_800   # 2026-09-01 00:00 UTC == the August scan end
    assert prog.resolve(SUSDS, 0, 9_000, end_ts_aug).txs == frozenset()
