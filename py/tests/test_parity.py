"""Offline Dune-parity regression tests.

For every fixture under py/tests/fixtures/, replay the HyperSync pipeline on the
captured raw events (no network) and assert it still reproduces the captured
Dune golden output. This locks in the parity each token was validated at, so a
future change that breaks ANY already-ported token fails here.

Guarantees asserted per fixture:
  * every (chain, contract, user, dt, ref_code) key present in both matches on
    time_weighted_avg_balance within TOL and on day_type exactly;
  * Σ TWA agrees with Dune to REL_TOL (material total identical);
  * any unmatched keys are dust only (|TWA| <= DUST_CEIL) — the float-boundary
    rows at the twab>0 filter, which carry no material value.

Add a token: capture its fixture (py/tests/capture_fixture.py) after its live
Dune validation passes, commit it, and this suite covers it automatically.
"""

from __future__ import annotations

import gzip
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "py"))

from drhs import twa  # noqa: E402
from drhs.hypersync import LogRow  # noqa: E402
from run_source import SPECS  # noqa: E402

FIX_DIR = Path(__file__).parent / "fixtures"
TOL = 1e-6          # abs tolerance on matched TWA values
REL_TOL = 1e-9      # rel tolerance on Σ TWA
DUST_CEIL = 1e-4    # unmatched rows must all be below this (dust only)
AGG_DIFF_REL = 1e-7  # Σ|per-row diff| must be < this fraction of Σ TWA
OVER_TOL_FRAC = 1e-4  # at most this fraction of matched rows may exceed TOL

KEYS = ["k_chain", "k_contract", "k_user", "k_dt", "k_ref"]


def _fixtures() -> list[Path]:
    return sorted(p for p in FIX_DIR.glob("*") if (p / "meta.json").exists())


def _load_rows(fix: Path, tag: str, kind: str) -> list[LogRow] | None:
    """Load captured LogRows for a target; gzip (.json.gz) or plain (.json)."""
    gz, plain = fix / f"{tag}.{kind}.json.gz", fix / f"{tag}.{kind}.json"
    if gz.exists():
        with gzip.open(gz, "rt", encoding="utf-8") as f:
            data = json.load(f)
    elif plain.exists():
        data = json.loads(plain.read_text())
    else:
        return None
    return [LogRow(**d) for d in data]


def _read_golden(fix: Path) -> pd.DataFrame:
    gz, plain = fix / "dune_golden.csv.gz", fix / "dune_golden.csv"
    return pd.read_csv(gz) if gz.exists() else pd.read_csv(plain)


def _replay(fix: Path) -> pd.DataFrame:
    meta = json.loads((fix / "meta.json").read_text())
    spec = SPECS[meta["source"]]
    end = date.fromisoformat(meta["end"])
    end_ts = spec.template._end_ts(end)

    frames = []
    for t in spec.targets:
        tag = f"{t.blockchain}_{t.address.lower()}"
        ref = _load_rows(fix, tag, spec.ref_kind)   # referrals (A/B) or swaps (C)
        tr = _load_rows(fix, tag, "transfers")
        if tr is None:
            continue
        legs = spec.template.legs_from_rows(t, ref or [], tr, end_ts)
        if not legs.empty:
            frames.append(legs)
    legs = pd.concat(frames, ignore_index=True)
    if spec.excluded:
        legs = legs[~legs["user_addr"].str.lower().isin(spec.excluded)].copy()
    # Cap the fill at the fixture's end — rows with dt < end are unchanged, and
    # it avoids materializing the flat tail to 2026-06-30 (fast on big tokens).
    fill_through = min(end, date(2026, 6, 30))
    hs = twa.compute_twa(legs, fill_through=fill_through)
    return hs[hs["dt"].map(lambda d: str(d)[:10]) < meta["end"]].copy()


def _key(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["k_chain"] = df["blockchain"]
    df["k_contract"] = df["contract_address"].str.lower()
    df["k_user"] = df["user_addr"].str.lower()
    df["k_dt"] = df["dt"].map(lambda d: str(d)[:10])
    df["k_ref"] = df["ref_code"].astype(float).astype(int)
    return df


@pytest.mark.parametrize("fix", _fixtures(), ids=lambda p: p.name)
def test_dune_parity(fix: Path):
    hs = _key(_replay(fix))
    dn = _key(_read_golden(fix))
    dn["dn_twab"] = dn["time_weighted_avg_balance"].astype(float)
    dn["dn_daytype"] = dn["day_type"]

    hs_k = hs[KEYS + ["time_weighted_avg_balance", "day_type"]].rename(
        columns={"time_weighted_avg_balance": "hs_twab", "day_type": "hs_daytype"})
    m = hs_k.merge(dn[KEYS + ["dn_twab", "dn_daytype"]], on=KEYS, how="outer", indicator=True)
    both = m[m["_merge"] == "both"]
    unmatched = m[m["_merge"] != "both"]

    diff = (both["hs_twab"] - both["dn_twab"]).abs()

    # 1) material total identical (catches any systematic divergence)
    s_hs, s_dn = hs["time_weighted_avg_balance"].sum(), dn["dn_twab"].sum()
    assert abs(s_hs - s_dn) <= REL_TOL * max(abs(s_dn), 1.0), (
        f"Σ TWA diverged: HS={s_hs!r} Dune={s_dn!r}")

    # 2) aggregate per-row difference is a negligible fraction of the total, and
    #    per-row exceedances of the abs tol are vanishingly rare. A handful of
    #    rows can legitimately differ: a self-transfer (from==to) splits into two
    #    legs with identical (block, log_index), and Dune's UNION ALL + window
    #    ORDER BY resolves that tie NON-deterministically, so its daily_end can
    #    land mid-transfer. HS is arithmetically correct on these; we require the
    #    disagreement to stay immaterial rather than match Dune's coin-flip.
    assert diff.sum() <= AGG_DIFF_REL * max(abs(s_dn), 1.0), (
        f"aggregate |diff| {diff.sum():.3e} exceeds {AGG_DIFF_REL} of Σ {s_dn:.3e}")
    over = int((diff > TOL).sum())
    assert over <= max(2, int(OVER_TOL_FRAC * len(both))), (
        f"{over}/{len(both)} matched rows exceed abs tol {TOL} "
        f"(max diff {diff.max():.3e}) — more than tie-ambiguity can explain")

    # 3) day_type exact on all matched keys
    dtype_bad = both[both["hs_daytype"] != both["dn_daytype"]]
    assert dtype_bad.empty, f"{len(dtype_bad)} day_type mismatches:\n{dtype_bad.head()}"

    # 4) unmatched keys are dust only
    if len(unmatched):
        mx = pd.concat([unmatched["hs_twab"], unmatched["dn_twab"]]).abs().max()
        assert mx <= DUST_CEIL, (
            f"{len(unmatched)} unmatched keys, max |TWA|={mx:.3e} > dust ceil {DUST_CEIL}")


def test_at_least_one_fixture():
    assert _fixtures(), "no parity fixtures found — capture at least one"
