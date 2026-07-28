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
import pytest  # noqa: E402

from run_dr_chunk import (  # noqa: E402
    SHARD_RE, SHARDS, chunk_csv, chunk_plan, load_chunks, parse_shard,
)
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
    """Pins the REAL production regex against the real filename builder."""
    p = chunk_csv(Path("/x"), "susds_psm3_base_sUSDS", "3/4")
    m = SHARD_RE.match(p.stem)
    assert m and m.group(1) == "susds_psm3_base_sUSDS" and m.group(3) == "4"


def test_parse_shard_validation():
    assert parse_shard("3/8", 8) == (3, 8)
    for bad, n in (("8/8", 8), ("-1/8", 8), ("1/0", None), ("x/y", None), ("1/4", 8)):
        with pytest.raises(SystemExit):
            parse_shard(bad, n)


def test_sharding_sp_sources_is_refused():
    """deployment_ratios needs the full vault TWA; shard+sp must hard-fail."""
    from run_dr_chunk import compute_chunk
    with pytest.raises(SystemExit, match="not exact"):
        compute_chunk("sp_vaults_ethereum_spUSDC", "0/2", date(2026, 7, 1))


def test_load_chunks_guards(tmp_path):
    import pandas as pd
    row = "month,blockchain,token,ref_code,dr_usd,source\n2026-01-01,ethereum,sUSDS,99,1.0,psm3\n"
    (tmp_path / "chunk_a_s0of2.csv").write_text(row)
    (tmp_path / "chunk_a_s1of2.csv").write_text(row)
    df = load_chunks(tmp_path)
    assert float(df["dr_usd"].sum()) == 2.0
    # unsharded checkpoint beside its shard set -> stray error in strict mode
    (tmp_path / "chunk_a.csv").write_text(row)
    with pytest.raises(SystemExit, match="strays"):
        load_chunks(tmp_path, expected=[tmp_path / "chunk_a_s0of2.csv",
                                        tmp_path / "chunk_a_s1of2.csv"])
    (tmp_path / "chunk_a.csv").unlink()
    # mixed shard-N families -> error even without a plan
    (tmp_path / "chunk_a_s0of3.csv").write_text(row)
    with pytest.raises(SystemExit, match="mixed shard"):
        load_chunks(tmp_path)


def test_manifest_pins_end(tmp_path):
    from datetime import date as _d
    from run_dr_chunk import ensure_manifest
    ensure_manifest(tmp_path, _d(2026, 7, 1))
    ensure_manifest(tmp_path, _d(2026, 7, 1))      # same end: fine
    with pytest.raises(SystemExit, match="end="):
        ensure_manifest(tmp_path, _d(2026, 8, 1))  # different end: refuse


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
