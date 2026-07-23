"""Per-user daily time-weighted-average balance (TWA) with last-referral-wins
attribution — a faithful port of the shared TWA tail in
``queries/twa_stusds.sql`` (Spark's query_5358161).

Input: one row per **balance-change leg** — a signed change to a single user's
balance in a single event, already decimal-scaled, optionally carrying the
ref_code named for that user in the same transaction.

Output: the shared TWA schema, one row per (blockchain, contract, symbol, user,
dt, ref_code):

    blockchain, contract_address, symbol, user_addr, dt, ref_code,
    time_weighted_avg_balance, day_type, segment_duration_seconds,
    segment_balance_time_product

The port reproduces, in order:
  running_balances -> daily_end_balances -> (tx-day) daily_start_balances ->
  intra-day segments -> daily_referral_segments -> complete_user_dates
  (no-transaction-day gap fill, forward-filled ref+balance) -> twab>0 filter.

``compute_twa`` is a **vectorized** (pandas) implementation; ``compute_twa_loop``
is the original per-user Python reference kept for benchmarking / cross-checks.
Both are byte-equivalent (the offline fixtures gate this).

Knobs:
  * ``fill_through`` — the calendar day the no-transaction-day fill extends to
    for users still holding a balance (SQL ``least(current_date, 2026-06-30)``).
  * ``emit_from`` — output only rows with dt >= this date. Balances are still
    reconstructed from the FULL leg history (so values are correct); only the
    materialized/compared rows are restricted. This makes a "recent window" run
    cheap on high-volume tokens without changing any emitted value.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

SENTINEL = -999999
SECONDS_PER_DAY = 86400
_DUST = 1e-9

OUTPUT_COLUMNS = [
    "blockchain", "contract_address", "symbol", "user_addr", "dt", "ref_code",
    "time_weighted_avg_balance", "day_type",
    "segment_duration_seconds", "segment_balance_time_product",
]

G = ["blockchain", "contract_address", "user_addr"]


def _day(ts: int) -> date:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date()


def _midnight_ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


# ============================================================================
# Engine dispatch
# ============================================================================
def compute(
    legs: pd.DataFrame,
    *,
    fill_through: date = date(2026, 6, 30),
    emit_from: date | None = None,
    engine: str = "vector",
) -> pd.DataFrame:
    """Compute the TWA frame with the chosen engine.

    ``engine="vector"`` (default) is the fast pandas implementation; ``"loop"``
    is the per-user reference — slower but with a flat, tiny memory footprint,
    which is what you want on a memory-constrained box (high-volume sources
    materialize several full-size frames at once under the vectorized engine).
    Both are byte-equivalent. For the loop engine ``emit_from`` is applied as a
    post-filter (balances are still reconstructed from the full leg history).
    """
    if engine == "loop":
        df = compute_twa_loop(legs, fill_through=fill_through)
        if emit_from is not None and len(df):
            df = df[df["dt"] >= emit_from].reset_index(drop=True)
        return df
    if engine != "vector":
        raise ValueError(f"unknown TWA engine {engine!r} (expected 'vector' or 'loop')")
    return compute_twa(legs, fill_through=fill_through, emit_from=emit_from)


# ============================================================================
# Vectorized implementation
# ============================================================================
def compute_twa(
    legs: pd.DataFrame,
    *,
    fill_through: date = date(2026, 6, 30),
    emit_from: date | None = None,
) -> pd.DataFrame:
    if legs.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    df = legs[G + ["symbol", "block", "log_index", "ts", "amount_change", "ref_code"]].copy()
    df["ref_num"] = pd.to_numeric(df["ref_code"], errors="coerce")
    df = df.sort_values(G + ["block", "log_index"], kind="stable").reset_index(drop=True)

    gb = df.groupby(G, sort=False)
    df["running"] = gb["amount_change"].cumsum()
    df["ref_f"] = gb["ref_num"].ffill().fillna(SENTINEL).astype("int64")

    tsdt = pd.to_datetime(df["ts"], unit="s", utc=True)
    # naive ns-resolution date key (matches pd.date_range / merge_asof below)
    df["dt64"] = tsdt.dt.floor("D").dt.tz_localize(None).astype("datetime64[ns]")
    # midnight unix seconds — derive from the NAIVE dt64 (astype int64 on a
    # tz-aware datetime does not give epoch nanoseconds).
    df["mid"] = df["dt64"].astype("int64") // 10**9

    symbol_by_group = df.groupby(G, sort=False)["symbol"].first()

    # ---- daily_end_balances: last row per (user, dt) -----------------------
    de = df.groupby(G + ["dt64"], sort=False).tail(1)[G + ["dt64", "mid", "running", "ref_f"]].copy()
    de = de.rename(columns={"running": "end_bal", "ref_f": "end_ref"})
    de = de.sort_values(G + ["dt64"], kind="stable").reset_index(drop=True)

    # ---- daily_start (lag over tx days) ------------------------------------
    de_g = de.groupby(G, sort=False)
    de["start_bal"] = de_g["end_bal"].shift(1).fillna(0.0)
    de["start_ref"] = de_g["end_ref"].shift(1)

    # ---- intra-day segments -------------------------------------------------
    real = df[G + ["dt64", "mid", "ts", "block", "log_index", "running", "ref_f"]]
    syn = de[G + ["dt64", "mid", "start_bal", "start_ref"]].copy()
    syn["ts"] = syn["mid"]
    syn["block"] = 0
    syn["log_index"] = -1
    syn["running"] = syn["start_bal"]
    syn["ref_f"] = syn["start_ref"].fillna(SENTINEL).astype("int64")
    syn = syn[G + ["dt64", "mid", "ts", "block", "log_index", "running", "ref_f"]]

    allev = pd.concat([real, syn], ignore_index=True)
    allev = allev.sort_values(G + ["dt64", "block", "log_index"], kind="stable").reset_index(drop=True)
    nxt = allev.groupby(G + ["dt64"], sort=False)["ts"].shift(-1)
    allev["next_ts"] = nxt.fillna(allev["mid"] + SECONDS_PER_DAY)
    allev["dur"] = allev["next_ts"] - allev["ts"]
    allev["prod"] = allev["running"] * allev["dur"]
    seg = (allev.groupby(G + ["dt64", "ref_f"], sort=False)
           .agg(seg_prod=("prod", "sum"), seg_dur=("dur", "sum")).reset_index())
    seg["twa"] = seg["seg_prod"] / SECONDS_PER_DAY

    # ---- user date ranges (trim exited users; else fill to fill_through) ----
    fill_ts = pd.Timestamp(fill_through)
    agg = de.groupby(G, sort=False).agg(
        first_dt=("dt64", "min"), last_tx=("dt64", "max"),
        final_bal=("end_bal", "last")).reset_index()
    agg["last_day"] = np.where(
        agg["final_bal"] > _DUST,
        np.maximum(agg["last_tx"].values, np.datetime64(fill_ts)),
        agg["last_tx"].values)
    agg["last_day"] = pd.to_datetime(agg["last_day"])

    # ---- transaction-day output rows ---------------------------------------
    seg_out = seg.rename(columns={"ref_f": "ref_code"})
    seg_out["day_type"] = "transaction_day"
    seg_out = seg_out.rename(columns={"twa": "time_weighted_avg_balance",
                                      "seg_dur": "segment_duration_seconds",
                                      "seg_prod": "segment_balance_time_product"})

    # ---- no-transaction-day rows (calendar minus tx days, ffilled) ---------
    notx = _no_tx_rows(de, agg, emit_from)

    # ---- assemble -----------------------------------------------------------
    out = pd.concat([
        seg_out[G + ["dt64", "ref_code", "time_weighted_avg_balance", "day_type",
                     "segment_duration_seconds", "segment_balance_time_product"]],
        notx,
    ], ignore_index=True)

    if emit_from is not None:
        out = out[out["dt64"] >= pd.Timestamp(emit_from)]
    out = out[out["time_weighted_avg_balance"] > 0].copy()
    if out.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    out["symbol"] = out.set_index(G).index.map(symbol_by_group).values
    out["dt"] = out["dt64"].dt.date
    out = out.sort_values(G + ["dt", "ref_code"]).reset_index(drop=True)
    return out[OUTPUT_COLUMNS]


def _no_tx_rows(de: pd.DataFrame, agg: pd.DataFrame, emit_from: date | None) -> pd.DataFrame:
    """Calendar days in [first, last_day] that are NOT tx days, with end_bal/
    end_ref forward-filled (via merge_asof) — one row per (user, day)."""
    rows = []
    ef = pd.Timestamp(emit_from) if emit_from is not None else None
    for r in agg.itertuples():
        key = (r.blockchain, r.contract_address, r.user_addr)
        cstart = r.first_dt if ef is None else max(r.first_dt, ef)
        if cstart > r.last_day:
            continue
        days = pd.date_range(cstart, r.last_day, freq="D")
        rows.append(pd.DataFrame({
            "blockchain": key[0], "contract_address": key[1], "user_addr": key[2],
            "dt64": days,
        }))
    if not rows:
        return pd.DataFrame(columns=G + ["dt64", "ref_code", "time_weighted_avg_balance",
                                         "day_type", "segment_duration_seconds",
                                         "segment_balance_time_product"])
    cal = pd.concat(rows, ignore_index=True)
    # anti-join tx days
    txkey = de[G + ["dt64"]].drop_duplicates()
    cal = cal.merge(txkey, on=G + ["dt64"], how="left", indicator=True)
    cal = cal[cal["_merge"] == "left_only"].drop(columns="_merge")
    if cal.empty:
        return pd.DataFrame(columns=G + ["dt64", "ref_code", "time_weighted_avg_balance",
                                         "day_type", "segment_duration_seconds",
                                         "segment_balance_time_product"])
    # forward-fill end_bal / end_ref as of each calendar day
    cal = cal.sort_values(["dt64"] + G, kind="stable")
    de_sorted = de.sort_values(["dt64"] + G, kind="stable")
    cal = pd.merge_asof(cal, de_sorted[G + ["dt64", "end_bal", "end_ref"]],
                        on="dt64", by=G, direction="backward")
    cal["ref_code"] = cal["end_ref"].fillna(SENTINEL).astype("int64")
    cal["time_weighted_avg_balance"] = cal["end_bal"].fillna(0.0)
    cal["day_type"] = "no_transaction_day"
    cal["segment_duration_seconds"] = np.nan
    cal["segment_balance_time_product"] = np.nan
    return cal[G + ["dt64", "ref_code", "time_weighted_avg_balance", "day_type",
                    "segment_duration_seconds", "segment_balance_time_product"]]


# ============================================================================
# Loop reference implementation (kept for benchmarking / cross-check)
# ============================================================================
def compute_twa_loop(legs: pd.DataFrame, *, fill_through: date = date(2026, 6, 30)) -> pd.DataFrame:
    if legs.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    out_rows: list[dict] = []
    for (blockchain, contract, user), gdf in legs.groupby(G, sort=False):
        symbol = gdf["symbol"].iloc[0]
        for r in _compute_user(gdf, fill_through):
            r.update(blockchain=blockchain, contract_address=contract, symbol=symbol, user_addr=user)
            out_rows.append(r)
    if not out_rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    df = pd.DataFrame(out_rows)
    df = df[df["time_weighted_avg_balance"] > 0].copy()
    df = df.sort_values(G + ["dt", "ref_code"]).reset_index(drop=True)
    return df[OUTPUT_COLUMNS]


def _compute_user(g: pd.DataFrame, fill_through: date) -> list[dict]:
    evs = g.sort_values(["block", "log_index"], kind="stable")
    running = 0.0
    cur_ref = SENTINEL
    events: list[dict] = []
    for blk, idx, ts, amt, ref in zip(evs["block"], evs["log_index"], evs["ts"],
                                      evs["amount_change"], evs["ref_code"]):
        running += float(amt)
        if pd.notna(ref):
            cur_ref = int(ref)
        events.append({"ts": int(ts), "block": int(blk), "log_index": int(idx),
                       "dt": _day(int(ts)), "running": running, "ref": cur_ref})
    end_bal: dict[date, float] = {}
    end_ref: dict[date, int] = {}
    for e in events:
        end_bal[e["dt"]] = e["running"]
        end_ref[e["dt"]] = e["ref"]
    tx_days = sorted(end_bal.keys())
    start_bal: dict[date, float] = {}
    start_ref: dict[date, int | None] = {}
    prev_bal = 0.0
    prev_ref: int | None = None
    for d in tx_days:
        start_bal[d] = prev_bal
        start_ref[d] = prev_ref
        prev_bal = end_bal[d]
        prev_ref = end_ref[d]
    segments: dict[date, dict[int, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))
    events_by_day: dict[date, list[dict]] = defaultdict(list)
    for e in events:
        events_by_day[e["dt"]].append(e)
    for d in tx_days:
        day_events = list(events_by_day[d])
        synthetic = {"ts": _midnight_ts(d), "block": 0, "log_index": -1,
                     "running": start_bal[d],
                     "ref": start_ref[d] if start_ref[d] is not None else SENTINEL}
        seq = [synthetic] + day_events
        seq.sort(key=lambda x: (x["block"], x["log_index"]))
        next_midnight = _midnight_ts(d + timedelta(days=1))
        for i, e in enumerate(seq):
            nxt_ts = seq[i + 1]["ts"] if i + 1 < len(seq) else next_midnight
            dur = nxt_ts - e["ts"]
            if dur == 0:
                continue
            slot = segments[d][e["ref"]]
            slot[1] += dur
            slot[2] += e["running"] * dur
        for ref, slot in segments[d].items():
            slot[0] = slot[2] / SECONDS_PER_DAY
    final_balance = end_bal[tx_days[-1]]
    first_day = tx_days[0]
    last_day = max(tx_days[-1], fill_through) if final_balance > _DUST else tx_days[-1]
    out: list[dict] = []
    ff_bal: float | None = None
    ff_ref: int | None = None
    d = first_day
    one = timedelta(days=1)
    while d <= last_day:
        if d in segments:
            for ref, (twab, seg_dur, seg_prod) in segments[d].items():
                out.append({"dt": d, "ref_code": ref, "time_weighted_avg_balance": twab,
                            "day_type": "transaction_day", "segment_duration_seconds": seg_dur,
                            "segment_balance_time_product": seg_prod})
            ff_bal = end_bal[d]
            ff_ref = end_ref[d]
        else:
            out.append({"dt": d, "ref_code": ff_ref if ff_ref is not None else SENTINEL,
                        "time_weighted_avg_balance": ff_bal if ff_bal is not None else 0.0,
                        "day_type": "no_transaction_day", "segment_duration_seconds": None,
                        "segment_balance_time_product": None})
        d += one
    return out
