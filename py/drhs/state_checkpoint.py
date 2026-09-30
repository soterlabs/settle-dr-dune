"""Month-boundary state for incremental DR calculation.

The monthly reward CSV/Parquet is an aggregate and cannot seed the next run.
This module persists the complete state consumed by the TWA engine: closing
balance and sticky last-referral code for every holder, including zero-balance
holders whose old code becomes active again on an untagged redeposit.

Snapshots are taken at an exclusive UTC cutoff.  A synthetic leg at that
cutoff recreates the exact opening state; only subsequent on-chain legs then
need to be fetched and evaluated.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from .twa import G, SENTINEL

STATE_COLUMNS = [
    "blockchain", "contract_address", "symbol", "user_addr",
    "balance", "ref_code",
]
LEG_COLUMNS = [
    "blockchain", "contract_address", "symbol", "user_addr", "block",
    "log_index", "ts", "amount_change", "ref_code",
]
STATE_FORMAT = "dr-month-end-state-v1"
FIRST_CUTOFF = date(2026, 9, 1)  # closing state for August 2026


class FullReplayRequired(RuntimeError):
    """An incremental source invariant changed and its history must be replayed."""


def previous_month_cutoff(cutoff: date) -> date:
    """The immediately preceding UTC month boundary."""
    if cutoff.day != 1:
        raise ValueError(f"state checkpoints require a first-of-month cutoff: {cutoff}")
    return date(cutoff.year - (cutoff.month == 1),
                12 if cutoff.month == 1 else cutoff.month - 1, 1)


def job_key(name: str, shard: str | None) -> str:
    return name + (f"_s{shard.replace('/', 'of')}" if shard else "")


def paths(root: Path, cutoff: date, name: str, shard: str | None) -> tuple[Path, Path, Path]:
    directory = root / cutoff.isoformat()
    key = job_key(name, shard)
    return (directory / f"{key}.state.parquet",
            directory / f"{key}.monthly.parquet",
            directory / f"{key}.json")


def atomic_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, path)


def write_snapshot(root: Path, cutoff: date, name: str, shard: str | None,
                   state: pd.DataFrame, monthly: pd.DataFrame,
                   fingerprint: str) -> None:
    """Atomically publish one job's state and cumulative monthly output."""
    state_path, monthly_path, meta_path = paths(root, cutoff, name, shard)
    # Metadata is the commit marker. Invalidate an older triplet before either
    # data file is replaced, so interruption can only leave an incomplete set.
    if meta_path.exists():
        meta_path.unlink()
    atomic_parquet(state[STATE_COLUMNS], state_path)
    atomic_parquet(monthly, monthly_path)
    metadata = {
        "format": STATE_FORMAT,
        "cutoff": cutoff.isoformat(),
        "chunk": name,
        "shard": shard,
        "fingerprint": fingerprint,
        "state_rows": len(state),
        "monthly_rows": len(monthly),
    }
    tmp = meta_path.with_suffix(meta_path.suffix + ".tmp")
    tmp.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, meta_path)


def snapshot_complete(root: Path, cutoff: date, name: str, shard: str | None,
                      fingerprint: str) -> bool:
    """Cheap integrity check that does not load a potentially large state."""
    state_path, monthly_path, meta_path = paths(root, cutoff, name, shard)
    if not (state_path.exists() and monthly_path.exists() and meta_path.exists()):
        return False
    try:
        metadata = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return all((
        metadata.get("format") == STATE_FORMAT,
        metadata.get("cutoff") == cutoff.isoformat(),
        metadata.get("chunk") == name,
        metadata.get("shard") == shard,
        metadata.get("fingerprint") == fingerprint,
    ))


def load_snapshot(root: Path, cutoff: date, name: str, shard: str | None,
                  fingerprint: str):
    """Return ``(state, cumulative monthly)`` or ``None`` if incomplete.

    Metadata is published last, so a killed writer cannot make a partial pair
    appear valid. A methodology-fingerprint mismatch returns ``None`` and
    triggers a safe replay; malformed identity/format metadata is a hard error.
    """
    state_path, monthly_path, meta_path = paths(root, cutoff, name, shard)
    if not (state_path.exists() and monthly_path.exists() and meta_path.exists()):
        return None
    metadata = json.loads(meta_path.read_text())
    # A code/configuration change makes the old state unsafe but not corrupt:
    # report it as unavailable so the worker performs its documented replay.
    if metadata.get("fingerprint") != fingerprint:
        return None
    expected = {
        "format": STATE_FORMAT,
        "cutoff": cutoff.isoformat(),
        "chunk": name,
        "shard": shard,
        "fingerprint": fingerprint,
    }
    bad = {key: (metadata.get(key), value) for key, value in expected.items()
           if metadata.get(key) != value}
    if bad:
        raise ValueError(f"incompatible state checkpoint {meta_path}: {bad}")
    state = pd.read_parquet(state_path)
    missing = set(STATE_COLUMNS) - set(state.columns)
    if missing:
        raise ValueError(f"state checkpoint {state_path} lacks columns {sorted(missing)}")
    monthly = pd.read_parquet(monthly_path)
    return state[STATE_COLUMNS], monthly


def closing_state(legs: pd.DataFrame, opening: pd.DataFrame | None = None) -> pd.DataFrame:
    """Advance ``opening`` through ordered ``legs`` and return closing state."""
    state: dict[tuple[str, str, str], list] = {}
    if opening is not None and not opening.empty:
        for r in opening.itertuples(index=False):
            state[(r.blockchain, r.contract_address, r.user_addr)] = [
                r.symbol, float(r.balance), int(r.ref_code),
            ]
    if not legs.empty:
        ordered = legs.sort_values([*G, "block", "log_index"], kind="stable")
        for r in ordered.itertuples(index=False):
            key = (r.blockchain, r.contract_address, r.user_addr)
            cur = state.setdefault(key, [r.symbol, 0.0, SENTINEL])
            if cur[0] != r.symbol:
                raise ValueError(f"symbol changed inside checkpoint key {key}")
            cur[1] += float(r.amount_change)
            if pd.notna(r.ref_code):
                cur[2] = int(r.ref_code)
    rows = [dict(blockchain=k[0], contract_address=k[1], symbol=v[0],
                 user_addr=k[2], balance=v[1], ref_code=v[2])
            for k, v in state.items()]
    if not rows:
        return pd.DataFrame(columns=STATE_COLUMNS)
    return (pd.DataFrame(rows)[STATE_COLUMNS]
            .sort_values(G, kind="stable").reset_index(drop=True))


def opening_legs(state: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    """Represent a snapshot as midnight legs accepted by ``compute_twa``."""
    if state.empty:
        return pd.DataFrame(columns=LEG_COLUMNS)
    ts = int(datetime(cutoff.year, cutoff.month, cutoff.day,
                      tzinfo=timezone.utc).timestamp())
    out = state.rename(columns={"balance": "amount_change"}).copy()
    out["block"] = 0
    out["log_index"] = -1
    out["ts"] = ts
    return out[LEG_COLUMNS]


def incremental_legs(state: pd.DataFrame, legs: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    """Opening snapshot legs followed by the new interval's real legs."""
    seed = opening_legs(state, cutoff)
    frames = [f for f in (seed, legs) if not f.empty]
    return (pd.concat(frames, ignore_index=True) if frames
            else pd.DataFrame(columns=LEG_COLUMNS))
