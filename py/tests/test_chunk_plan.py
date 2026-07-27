"""Chunk registry + class-D holder source tests (docs/prd-chunked-pipeline.md).

The chunk plan is derived from pipeline.SOURCE_MONTHLY x run_source.SPECS —
these tests pin the 1:1 target<->chunk mapping so a new target can never be
silently dropped from (or duplicated in) a chunked run, and cover the pure
legs logic of the Template F holder source.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "py"))

from drhs import events  # noqa: E402
from drhs.hypersync import LogRow  # noqa: E402
from drhs.revenue import pipeline  # noqa: E402
from drhs.sources import holder  # noqa: E402
from run_dr_chunk import SHARDS, chunk_csv, chunk_plan  # noqa: E402
from run_source import SPECS  # noqa: E402


def test_every_target_maps_to_exactly_one_chunk():
    plan = chunk_plan()
    planned = [(src, id(t)) for _fam, src, t, _n in plan.values()]
    expected = [(src, id(t))
                for _fam, (srcs, *_r) in pipeline.SOURCE_MONTHLY.items()
                for src in srcs
                for t in SPECS[src].targets]
    assert sorted(planned) == sorted(expected)
    assert len(plan) == len(expected)          # unique names, nothing merged


def test_families_filter_and_holder_sources_present():
    plan = chunk_plan()
    names = set(plan)
    assert "usds_aave_ethereum_USDS" in names
    assert "usds_ref4001_ethereum_USDS" in names
    only_sp = chunk_plan(["sp"])
    assert all(v[0] == "sp" for v in only_sp.values())
    assert len(only_sp) == len(SPECS["sp_vaults"].targets)


def test_sharded_targets_exist_in_specs():
    """A SHARDS key that matches no real target is a silent no-op — forbid."""
    keys = {(src, t.blockchain, t.symbol)
            for _fam, (srcs, *_r) in pipeline.SOURCE_MONTHLY.items()
            for src in srcs for t in SPECS[src].targets}
    assert set(SHARDS) <= keys


def test_chunk_csv_names_are_shard_guard_compatible():
    import re
    p = chunk_csv(Path("/x"), "susds_psm3_base_sUSDS", "3/4")
    m = re.match(r"chunk_(.+)_s(\d+)of(\d+)$", p.stem)
    assert m and m.group(1) == "susds_psm3_base_sUSDS" and m.group(3) == "4"


# --- Template F holder source (pure legs) --------------------------------------

H = holder.AAVE_USDS
OTHER = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
DAY = 1735689600  # 2025-01-01 UTC


def _tr(tx, li, frm, to, amount, ts=DAY):
    return LogRow(block_number=100, log_index=li, block_time=ts,
                  address=H.token, topic0=events.TRANSFER_TOPIC0,
                  topic1=events.addr_to_topic(frm), topic2=events.addr_to_topic(to),
                  topic3=None, data="0x" + format(int(amount * 10**18), "064x"),
                  transaction_hash=tx)


def test_holder_legs_signs_and_code():
    rows = [_tr("0xt1", 1, OTHER, H.holder, 100.0),   # inflow  -> +100
            _tr("0xt2", 2, H.holder, OTHER, 40.0),    # outflow -> -40
            _tr("0xt3", 3, H.holder, H.holder, 5.0),  # self-transfer -> dropped
            _tr("0xt4", 4, OTHER, OTHER, 7.0)]        # unrelated -> dropped
    legs = holder.legs_from_rows(H, rows, DAY + 86400)
    assert list(legs["amount_change"]) == [100.0, -40.0]
    assert set(legs["user_addr"]) == {H.holder}
    assert set(legs["ref_code"]) == {9001}


def test_holder_legs_window_and_empty():
    rows = [_tr("0xt1", 1, OTHER, H.holder, 100.0, ts=DAY + 86400)]
    legs = holder.legs_from_rows(H, rows, end_ts=DAY + 86400)  # ts >= end -> out
    assert legs.empty


def test_holder_full_balance_attribution_end_to_end():
    from drhs import twa
    rows = [_tr("0xt1", 1, OTHER, H.holder, 100.0)]
    legs = holder.legs_from_rows(H, rows, DAY + 2 * 86400)
    out = twa.compute_twa(legs, fill_through=date(2025, 1, 2))
    assert set(out["ref_code"]) == {9001}
    assert (out["user_addr"] == H.holder).all()
