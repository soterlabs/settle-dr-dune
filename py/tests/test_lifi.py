"""Offline tests for the Li.Fi integrator-anchored program (drhs.sources.lifi).

Decoder vectors are the raw logs of the two txs the program was specified
from (base 0xfadc262b… origin, ethereum 0x1edf4214… destination, 2026-07-30).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "py"))

from drhs.hypersync import LogRow  # noqa: E402
from drhs.sources import lifi  # noqa: E402

TX_ID = "0xea31aac263445c39865f4a68b50c760564b5f10e17ff498dccfcdff89dab028a"
USER = "0x21e7105b9e85fa4524fc25389604ae98baed7d29"
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"

# base 0xfadc262b… log 311 — LiFiTransferStarted(BridgeData)
STARTED_DATA = "0x" + "".join([
    "0000000000000000000000000000000000000000000000000000000000000020",
    "ea31aac263445c39865f4a68b50c760564b5f10e17ff498dccfcdff89dab028a",
    "0000000000000000000000000000000000000000000000000000000000000140",
    "0000000000000000000000000000000000000000000000000000000000000180",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "000000000000000000000000833589fcd6edb6e08f4c7c32d4f71b54bda02913",
    "00000000000000000000000021e7105b9e85fa4524fc25389604ae98baed7d29",
    "00000000000000000000000000000000000000000000000000000000000f387c",
    "0000000000000000000000000000000000000000000000000000000000000001",
    "0000000000000000000000000000000000000000000000000000000000000001",
    "0000000000000000000000000000000000000000000000000000000000000001",
    "000000000000000000000000000000000000000000000000000000000000000a",
    "7374617267617465563200000000000000000000000000000000000000000000",
    "000000000000000000000000000000000000000000000000000000000000000d",
    "6f7365726f66726f6e74656e6400000000000000000000000000000000000000",
])
# ethereum 0x1edf4214… log 211 — AssetSwapped (Executor), USDS -> sUSDS
ASSET_SWAPPED_DATA = "0x" + "".join([
    "ea31aac263445c39865f4a68b50c760564b5f10e17ff498dccfcdff89dab028a",
    "0000000000000000000000009f12686cba2383150e869a8e5c2ededdcf7ab4f9",
    "000000000000000000000000dc035d45d973e3ec169d2276ddab16f1e407384f",
    "000000000000000000000000a3931d71877c0e7a3148cb7eb4463524fec27fbd",
    "0000000000000000000000000000000000000000000000000dd82ecec11b02bb",
    "0000000000000000000000000000000000000000000000000c8844f295c18de9",
    "000000000000000000000000000000000000000000000000000000006a6bd04f",
])


def _row(address: str, topic0: str, data: str, topic1: str | None = None) -> LogRow:
    return LogRow(block_number=1, log_index=0, block_time=1785450483, address=address,
                  topic0=topic0, topic1=topic1, topic2=None, topic3=None, data=data,
                  transaction_hash="0xt")


def test_decode_transfer_started_real_vector():
    d = lifi.decode_transfer_started(
        _row(lifi.LIFI_DIAMOND, lifi.TRANSFER_STARTED_TOPIC0, STARTED_DATA))
    assert d.transaction_id == TX_ID
    assert d.bridge == "stargateV2"
    assert d.integrator == "oserofrontend"
    assert d.receiver == USER
    assert d.sending_asset == USDC_BASE
    assert d.destination_chain_id == 1
    assert d.has_destination_call is True


def test_asset_swapped_transaction_id_real_vector():
    r = _row(lifi.LIFI_EXECUTOR, lifi.ASSET_SWAPPED_TOPIC0, ASSET_SWAPPED_DATA)
    assert lifi.asset_swapped_transaction_id(r) == TX_ID


def test_decode_generic_swap_layout():
    """Flat-arg layout: (string, string, address, address, address, uint, uint).
    Built from the ABI spec (no oserofrontend same-chain tx captured yet);
    pinned so a layout slip in the decoder cannot pass silently."""
    integ, ref = b"oserofrontend", b""
    head = [
        f"{7 * 32:064x}",                      # offset(integrator) = after 7 head words
        f"{7 * 32 + 64:064x}",                 # offset(referrer)   = +len word +1 data word
        USER[2:].rjust(64, "0"),
        USDC_BASE[2:].rjust(64, "0"),
        "a3931d71877c0e7a3148cb7eb4463524fec27fbd".rjust(64, "0"),
        f"{1_000_000:064x}",
        f"{123456789:064x}",
    ]
    tail = [f"{len(integ):064x}", integ.hex().ljust(64, "0"), f"{len(ref):064x}"]
    data = "0x" + "".join(head + tail)
    d = lifi.decode_generic_swap(
        _row(lifi.LIFI_DIAMOND, lifi.GENERIC_SWAP_COMPLETED_TOPIC0, data, topic1=TX_ID))
    assert d.transaction_id == TX_ID
    assert d.integrator == "oserofrontend"
    assert d.receiver == USER
    assert d.to_asset == "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd"
    assert d.to_amount == 123456789


# --- anchoring + the tx-restricted program --------------------------------------
from datetime import date  # noqa: E402

from drhs import events, twa  # noqa: E402
from drhs.sources import template_ab  # noqa: E402
from drhs.sources.template_ab import SyntheticProgram, synthetic_referrals  # noqa: E402

SUSDS = template_ab.SUSDS_ETH
OTHER = "0x00000000000000000000000000000000000000ee"
DAY = 1785450483  # 2026-07-30


def _generic(tx, integrator, receiver=USER):
    integ = integrator.encode()
    head = [f"{7 * 32:064x}", f"{7 * 32 + 64 + 32 * ((len(integ) + 31) // 32):064x}",
            receiver[2:].rjust(64, "0"), USDC_BASE[2:].rjust(64, "0"),
            SUSDS.address[2:].rjust(64, "0"), f"{1:064x}", f"{1:064x}"]
    tail = [f"{len(integ):064x}", integ.hex().ljust(64 * ((len(integ) + 31) // 32), "0"), f"{0:064x}"]
    r = _row(lifi.LIFI_DIAMOND, lifi.GENERIC_SWAP_COMPLETED_TOPIC0, "0x" + "".join(head + tail), topic1=TX_ID)
    return LogRow(**{**r.__dict__, "transaction_hash": tx})


def _started(tx, integrator="oserofrontend", dest=1):
    # patch integrator + destination into the real base vector
    h = STARTED_DATA[2:]
    integ = integrator.encode().hex().ljust(64, "0")
    h = h[:14 * 64] + integ            # word 14 = integrator bytes
    h = h[:13 * 64] + f"{len(integrator):064x}" + h[14 * 64:]
    h = h[:8 * 64] + f"{dest:064x}" + h[9 * 64:]
    r = _row(lifi.LIFI_DIAMOND, lifi.TRANSFER_STARTED_TOPIC0, "0x" + h)
    return LogRow(**{**r.__dict__, "transaction_hash": tx})


def _completed(tx, tx_id=TX_ID, emitter=lifi.LIFI_EXECUTOR):
    r = _row(emitter, lifi.TRANSFER_COMPLETED_TOPIC0, "0x" + "00" * 128, topic1=tx_id)
    return LogRow(**{**r.__dict__, "transaction_hash": tx})


def _tr(tx, li, frm, to, amount, ts=DAY):
    return LogRow(block_number=100, log_index=li, block_time=ts, address=SUSDS.address,
                  topic0=events.TRANSFER_TOPIC0, topic1=events.addr_to_topic(frm),
                  topic2=events.addr_to_topic(to), topic3=None,
                  data="0x" + format(int(amount * 10**18), "064x"), transaction_hash=tx)


P = lifi.OSERO_FRONTEND


def test_anchor_same_chain_by_integrator():
    txs, contracts = lifi.anchored_deliveries(
        P, "ethereum", [_generic("0xa", "oserofrontend"), _generic("0xb", "jumper.exchange"),
                        _generic("0xc", "OseroFrontend")], [], [], [])
    assert txs == {"0xa", "0xc"}                    # case-insensitive
    assert lifi.LIFI_DIAMOND in contracts and lifi.LIFI_EXECUTOR in contracts


def test_anchor_cross_chain_joins_on_transaction_id():
    started = [_started("0xorigin1", "oserofrontend", dest=1),      # base -> ethereum
               _started("0xorigin2", "oserofrontend", dest=42161),  # base -> arbitrum: not ours
               _started("0xorigin3", "jumper.exchange", dest=1)]    # other integrator
    completed = [_completed("0xdest1")]                             # carries TX_ID
    txs, contracts = lifi.anchored_deliveries(P, "ethereum", [], started, completed, [])
    assert txs == {"0xdest1"}
    # another integrator's bridge never anchors, even when a completion carries its id
    txs2, _ = lifi.anchored_deliveries(P, "ethereum", [], started[2:], completed, [])
    assert txs2 == frozenset()
    # and a bridge to a different destination anchors only on THAT chain
    txs3, _ = lifi.anchored_deliveries(P, "arbitrum", [], started[:1], completed, [])
    assert txs3 == frozenset()
    # a completion from an UNKNOWN emitter never anchors (spoofable: the id is
    # public on the origin chain) — it is logged and the delivery set stays the
    # verified allowlist
    rcv = "0x4dac9d1769b9b304cb04741dcdeb2fc14abdf110"
    txs3, contracts3 = lifi.anchored_deliveries(P, "ethereum", [], started[:1], [_completed("0xd", emitter=rcv)], [])
    assert txs3 == frozenset() and rcv not in contracts3
    assert contracts3 == {lifi.LIFI_DIAMOND, lifi.LIFI_EXECUTOR} | set(lifi.LIFI_RECEIVERS)


def test_asset_swapped_fallback_anchors_too():
    started = [_started("0xo", "oserofrontend", dest=1)]
    sw = LogRow(**{**_row(lifi.LIFI_EXECUTOR, lifi.ASSET_SWAPPED_TOPIC0, ASSET_SWAPPED_DATA).__dict__,
                   "transaction_hash": "0xdest"})
    txs, _ = lifi.anchored_deliveries(P, "ethereum", [], started, [], [sw])
    assert txs == {"0xdest"}


def test_tx_restricted_program_tags_only_anchored_txs():
    prog = SyntheticProgram("lifi", 3900, frozenset({lifi.LIFI_DIAMOND, lifi.LIFI_EXECUTOR}),
                            txs=frozenset({"0xanchored"}))
    rows = [_tr("0xanchored", 1, lifi.LIFI_EXECUTOR, USER, 10.0),
            _tr("0xjumper", 1, lifi.LIFI_DIAMOND, OTHER, 10.0)]     # a Diamond delivery NOT ours
    assert synthetic_referrals(rows, (prog,)) == {("0xanchored", USER): (1, 3900)}


def test_unrestricted_program_unchanged():
    """txs=None keeps the CowSwap semantics byte-identical."""
    rows = [_tr("0xt", 1, "0x9008d19f58aabd9ed0d60971565aa8510560ab41", USER, 1.0)]
    assert synthetic_referrals(rows, (template_ab.COWSWAP,)) == {("0xt", USER): (1, 1003)}


def test_two_programs_may_share_a_contract():
    a = SyntheticProgram("a", 3900, frozenset({lifi.LIFI_DIAMOND}), txs=frozenset({"0x1"}))
    b = SyntheticProgram("b", 3901, frozenset({lifi.LIFI_DIAMOND}), txs=frozenset({"0x2"}))
    rows = [_tr("0x1", 1, lifi.LIFI_DIAMOND, USER, 1.0), _tr("0x2", 1, lifi.LIFI_DIAMOND, OTHER, 1.0)]
    assert synthetic_referrals(rows, (a, b)) == {("0x1", USER): (1, 3900), ("0x2", OTHER): (1, 3901)}


def test_real_referral_override_is_logged(caplog):
    pseudo = {("0xt", USER): (1, 3900)}
    real = {("0xt", USER): (2, 4012)}
    with caplog.at_level("WARNING"):
        merged = template_ab.merge_referrals(real, pseudo)
    assert merged[("0xt", USER)][1] == 4012          # real still wins
    assert any("overridden by a real Referral" in m for m in caplog.messages)


def test_resolve_offline(monkeypatch, tmp_path):
    """resolve() end to end with HyperSync stubbed: a same-chain oserofrontend
    swap on ethereum plus a base->ethereum bridge; a jumper swap is ignored."""
    monkeypatch.setenv("DRHS_CACHE_DIR", str(tmp_path))
    canned = {
        ("ethereum", lifi.GENERIC_SWAP_COMPLETED_TOPIC0): [_generic("0xsame", "oserofrontend"),
                                                           _generic("0xjump", "jumper.exchange")],
        ("base", lifi.TRANSFER_STARTED_TOPIC0): [_started("0xorig", "oserofrontend", dest=1)],
        ("ethereum", lifi.TRANSFER_COMPLETED_TOPIC0): [_completed("0xdest")],
        ("ethereum", lifi.ASSET_SWAPPED_TOPIC0): [],
    }
    def fake_query(chain, selections, fb, tb, **kw):
        t0 = selections[0]["topics"][0][0]
        return hypersync_mod.QueryResult(rows=list(canned.get((chain, t0), [])))
    from drhs import hypersync as hypersync_mod
    monkeypatch.setattr(hypersync_mod, "query_logs", fake_query)
    monkeypatch.setattr(hypersync_mod, "find_block_at_or_before", lambda chain, ts: 1_000)
    prog = lifi.IntegratorProgram("t", 3900, "oserofrontend", origin_chains=("base", "ethereum"))
    resolved = prog.resolve(SUSDS, 0, 2_000, DAY + 1)
    assert isinstance(resolved, SyntheticProgram)
    assert resolved.ref_code == 3900
    assert resolved.txs == {"0xsame", "0xdest"}
    assert lifi.LIFI_DIAMOND in resolved.contracts


def test_program_wired_on_susds_eth():
    from run_source import SPECS
    names = [getattr(p, "name", None) for p in SPECS["susds_eth"].synthetic]
    assert "lifi_oserofrontend" in names and "cowswap" in names


def test_blocks_for_only_falls_back_on_pre_genesis(monkeypatch):
    from drhs import hypersync as hs
    def boom(chain, ts):
        raise hs.HyperSyncError("HyperSync ethereum -> HTTP 503")
    monkeypatch.setattr(hs, "find_block_at_or_before", boom)
    import pytest
    with pytest.raises(hs.HyperSyncError, match="503"):
        lifi._blocks_for("ethereum", 1, 2)
    def genesis(chain, ts):
        if ts == 1:
            raise hs.HyperSyncError("find_block_at_or_before(unichain, ts=1): target precedes genesis.")
        return 42
    monkeypatch.setattr(hs, "find_block_at_or_before", genesis)
    assert lifi._blocks_for("unichain", 1, 3) == (0, 42)   # end_ts-1 = 2 resolves normally


def test_unknown_completion_emitter_is_logged_not_trusted(caplog):
    started = [_started("0xo", "oserofrontend", dest=1)]
    spoof = _completed("0xspoof", emitter="0x00000000000000000000000000000000000000bad")
    with caplog.at_level("WARNING"):
        txs, _ = lifi.anchored_deliveries(P, "ethereum", [], started, [spoof], [])
    assert txs == frozenset()
    assert any("UNKNOWN emitter" in m for m in caplog.messages)


def test_generic_and_started_rows_must_come_from_the_diamond():
    fake = LogRow(**{**_generic("0xa", "oserofrontend").__dict__, "address": "0x00000000000000000000000000000000000000bad"})
    txs, _ = lifi.anchored_deliveries(P, "ethereum", [fake], [], [], [])
    assert txs == frozenset()


def test_origin_scan_has_a_lead_margin_and_resolve_short_circuits(monkeypatch):
    """Origin chains are scanned from start - ORIGIN_LEAD_SECONDS (a bridge
    started just before the window can deliver inside it); a start at/after
    the scan end skips every query."""
    from drhs import hypersync as hs
    asked = []
    monkeypatch.setattr(hs, "block_at_or_genesis", lambda chain, ts: asked.append((chain, ts)) or 10)
    monkeypatch.setattr(hs, "find_block_at_or_before", lambda chain, ts: 20)
    monkeypatch.setattr(hs, "query_logs", lambda *a, **k: hs.QueryResult(rows=[]))
    prog = lifi.IntegratorProgram("t", 3900, "oserofrontend", origin_chains=("base",), start=date(2026, 7, 1))
    end_ts = 1_785_542_400  # 2026-08-01
    prog.resolve(SUSDS, 0, 100, end_ts)
    start_ts = 1_782_864_000  # 2026-07-01 00:00 UTC
    assert ("base", start_ts - lifi.ORIGIN_LEAD_SECONDS) in asked
    asked.clear()
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not query"))
    monkeypatch.setattr(hs, "query_logs", boom)
    late = lifi.IntegratorProgram("t", 3900, "oserofrontend", origin_chains=("base",), start=date(2026, 9, 1))
    assert late.resolve(SUSDS, 0, 100, end_ts).txs == frozenset()


def test_block_at_or_genesis(monkeypatch):
    from drhs import hypersync as hs
    import pytest
    def boom(chain, ts): raise hs.HyperSyncError("HyperSync ethereum -> HTTP 503")
    monkeypatch.setattr(hs, "find_block_at_or_before", boom)
    with pytest.raises(hs.HyperSyncError, match="503"):
        hs.block_at_or_genesis("ethereum", 1)
    monkeypatch.setattr(hs, "find_block_at_or_before",
                        lambda c, ts: (_ for _ in ()).throw(hs.HyperSyncError("find_block_at_or_before(unichain, ts=1): target precedes genesis.")))
    assert hs.block_at_or_genesis("unichain", 1) == 0
