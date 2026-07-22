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
from drhs.sources import template_ab  # noqa: E402
from run_source import SOURCES, SOURCE_EXCLUDED  # noqa: E402

FIX_DIR = Path(__file__).parent / "fixtures"
TOL = 1e-6          # abs tolerance on matched TWA values
REL_TOL = 1e-9      # rel tolerance on Σ TWA
DUST_CEIL = 1e-4    # unmatched rows must all be below this (dust only)

KEYS = ["k_chain", "k_contract", "k_user", "k_dt", "k_ref"]


def _fixtures() -> list[Path]:
    return sorted(p for p in FIX_DIR.glob("*") if (p / "meta.json").exists())


def _load_rows(path: Path) -> list[LogRow]:
    return [LogRow(**d) for d in json.loads(path.read_text())]


def _replay(fix: Path) -> pd.DataFrame:
    meta = json.loads((fix / "meta.json").read_text())
    end = date.fromisoformat(meta["end"])
    end_ts = template_ab._end_ts(end)
    targets = SOURCES[meta["source"]]
    excluded = SOURCE_EXCLUDED.get(meta["source"], frozenset())

    frames = []
    for t in targets:
        tag = f"{t.blockchain}_{t.address.lower()}"
        rf, tf = fix / f"{tag}.referrals.json", fix / f"{tag}.transfers.json"
        if not tf.exists():
            continue
        legs = template_ab.legs_from_rows(t, _load_rows(rf), _load_rows(tf), end_ts)
        if not legs.empty:
            frames.append(legs)
    legs = pd.concat(frames, ignore_index=True)
    if excluded:
        legs = legs[~legs["user_addr"].str.lower().isin(excluded)].copy()
    hs = twa.compute_twa(legs)
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
    dn = _key(pd.read_csv(fix / "dune_golden.csv"))
    dn["dn_twab"] = dn["time_weighted_avg_balance"].astype(float)
    dn["dn_daytype"] = dn["day_type"]

    hs_k = hs[KEYS + ["time_weighted_avg_balance", "day_type"]].rename(
        columns={"time_weighted_avg_balance": "hs_twab", "day_type": "hs_daytype"})
    m = hs_k.merge(dn[KEYS + ["dn_twab", "dn_daytype"]], on=KEYS, how="outer", indicator=True)
    both = m[m["_merge"] == "both"]
    unmatched = m[m["_merge"] != "both"]

    # 1) material total identical
    s_hs, s_dn = hs["time_weighted_avg_balance"].sum(), dn["dn_twab"].sum()
    assert abs(s_hs - s_dn) <= REL_TOL * max(abs(s_dn), 1.0), (
        f"Σ TWA diverged: HS={s_hs!r} Dune={s_dn!r}")

    # 2) every matched key within tolerance, day_type exact
    bad = both[(both["hs_twab"] - both["dn_twab"]).abs() > TOL]
    assert bad.empty, f"{len(bad)} matched keys exceed abs tol {TOL}:\n{bad.head()}"
    dtype_bad = both[both["hs_daytype"] != both["dn_daytype"]]
    assert dtype_bad.empty, f"{len(dtype_bad)} day_type mismatches:\n{dtype_bad.head()}"

    # 3) unmatched keys are dust only
    if len(unmatched):
        mx = pd.concat([unmatched["hs_twab"], unmatched["dn_twab"]]).abs().max()
        assert mx <= DUST_CEIL, (
            f"{len(unmatched)} unmatched keys, max |TWA|={mx:.3e} > dust ceil {DUST_CEIL}")


def test_at_least_one_fixture():
    assert _fixtures(), "no parity fixtures found — capture at least one"
