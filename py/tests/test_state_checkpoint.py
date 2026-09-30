from datetime import date, datetime, timezone
from pathlib import Path
import sys

import pandas as pd
import pandas.testing as pdt

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "py"))

from drhs import state_checkpoint, twa
import run_dr_chunk


def _ts(day: str, hour: int = 0) -> int:
    d = datetime.strptime(day, "%Y-%m-%d").replace(hour=hour, tzinfo=timezone.utc)
    return int(d.timestamp())


def _legs(rows) -> pd.DataFrame:
    return pd.DataFrame([
        dict(blockchain="ethereum", contract_address="0xtoken", symbol="sUSDS",
             user_addr=user, block=block, log_index=0, ts=_ts(day, hour),
             amount_change=amount, ref_code=ref)
        for user, block, day, hour, amount, ref in rows
    ], columns=state_checkpoint.LEG_COLUMNS)


def test_snapshot_replay_matches_full_history_and_preserves_zero_balance_ref():
    cutoff = date(2026, 9, 1)
    before = _legs([
        ("0x01", 1, "2026-08-01", 0, 100.0, 7),
        ("0x02", 2, "2026-08-15", 0, 50.0, 8),
        # Zero-balance holders must remain in state: an untagged redeposit
        # inherits the old code under last-referral-wins semantics.
        ("0x02", 3, "2026-08-20", 0, -50.0, pd.NA),
    ])
    after = _legs([
        ("0x02", 4, "2026-09-03", 12, 25.0, pd.NA),
        ("0x01", 5, "2026-09-10", 6, -40.0, 9),
    ])
    all_legs = pd.concat([before, after], ignore_index=True)
    opening = state_checkpoint.closing_state(before)
    assert opening.loc[opening.user_addr == "0x02", "balance"].item() == 0
    assert opening.loc[opening.user_addr == "0x02", "ref_code"].item() == 8

    full = twa.compute_twa(all_legs, fill_through=date(2026, 9, 30))
    resumed = twa.compute_twa(
        state_checkpoint.incremental_legs(opening, after, cutoff),
        fill_through=date(2026, 9, 30),
    )
    cols = ["blockchain", "contract_address", "symbol", "user_addr", "dt",
            "ref_code", "time_weighted_avg_balance"]
    full_sep = full[full.dt >= cutoff][cols].reset_index(drop=True)
    resumed = resumed[cols].reset_index(drop=True)
    pdt.assert_frame_equal(full_sep, resumed)

    expected_close = state_checkpoint.closing_state(all_legs)
    resumed_close = state_checkpoint.closing_state(after, opening)
    pdt.assert_frame_equal(expected_close, resumed_close)


def test_snapshot_round_trip_requires_complete_compatible_triplet(tmp_path):
    state = state_checkpoint.closing_state(_legs([
        ("0x01", 1, "2026-08-01", 0, 10.0, 7),
    ]))
    monthly = pd.DataFrame([{
        "month": date(2026, 8, 1), "blockchain": "ethereum", "token": "sUSDS",
        "ref_code": 7, "dr_usd": 1.25, "source": "susds_susdc",
    }])
    cutoff = date(2026, 9, 1)
    assert state_checkpoint.load_snapshot(tmp_path, cutoff, "job", None) is None
    state_checkpoint.write_snapshot(tmp_path, cutoff, "job", None, state, monthly)
    got_state, got_monthly = state_checkpoint.load_snapshot(
        tmp_path, cutoff, "job", None)
    pdt.assert_frame_equal(got_state, state)
    pdt.assert_frame_equal(got_monthly, monthly)

    meta = state_checkpoint.paths(tmp_path, cutoff, "job", None)[2]
    meta.unlink()
    assert state_checkpoint.load_snapshot(tmp_path, cutoff, "job", None) is None


def test_previous_month_cutoff_handles_year_boundary():
    assert state_checkpoint.previous_month_cutoff(date(2027, 1, 1)) == date(2026, 12, 1)


def test_chunk_resumes_immediately_prior_state_and_carries_monthly(tmp_path, monkeypatch):
    name = "usds_farms_ethereum_USDS-SKY"
    before = _legs([("0x01", 1, "2026-08-01", 0, 100.0, 7)])
    after = _legs([("0x01", 2, "2026-09-15", 12, -25.0, pd.NA)])
    opening = state_checkpoint.closing_state(before)
    prior = pd.DataFrame([{
        "month": "2026-08-01", "blockchain": "ethereum", "token": "USDS",
        "ref_code": 7, "dr_usd": 1.0, "source": "farms",
    }])
    state_checkpoint.write_snapshot(
        tmp_path, date(2026, 9, 1), name, None, opening, prior)
    seen = []

    def fake_build(_src, _target, _end, scan_start=None):
        seen.append(scan_start)
        return after

    current = pd.DataFrame([{
        "month": "2026-09-01", "blockchain": "ethereum", "token": "USDS",
        "ref_code": 7, "dr_usd": 2.0, "source": "farms",
    }])
    monkeypatch.setattr(run_dr_chunk, "build_target_legs", fake_build)
    monkeypatch.setattr(run_dr_chunk, "_monthly", lambda *_args: current)

    cumulative, closing, mode = run_dr_chunk.compute_chunk(
        name, None, date(2026, 10, 1), state_dir=tmp_path)
    assert seen == [date(2026, 9, 1)]
    assert mode == "incremental"
    assert cumulative.dr_usd.tolist() == [1.0, 2.0]
    assert closing.balance.item() == 75.0
    assert closing.ref_code.item() == 7
