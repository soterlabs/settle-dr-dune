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

Two knobs mirror the SQL exactly (defaults = the deployed values):
  * ``end_exclusive`` — events at/after this date are dropped (SQL
    ``evt_block_time < least(end_date, 2026-07-01)``).
  * ``fill_through`` — the calendar day the no-transaction-day fill extends to
    for users still holding a balance (SQL
    ``least(current_date, date '2026-06-30')``).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import pandas as pd

SENTINEL = -999999
SECONDS_PER_DAY = 86400
_DUST = 1e-9

OUTPUT_COLUMNS = [
    "blockchain", "contract_address", "symbol", "user_addr", "dt", "ref_code",
    "time_weighted_avg_balance", "day_type",
    "segment_duration_seconds", "segment_balance_time_product",
]


def _day(ts: int) -> date:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date()


def _midnight_ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def compute_twa(
    legs: pd.DataFrame,
    *,
    fill_through: date = date(2026, 6, 30),
) -> pd.DataFrame:
    """Compute the per-user daily TWA frame from balance-change legs.

    ``legs`` columns: blockchain, contract_address, symbol, user_addr, block,
    log_index, ts (unix int), amount_change (float, signed, decimal-scaled),
    ref_code (int or NA). Rows are grouped by (blockchain, contract_address,
    user_addr); ``symbol`` is carried through (constant per contract).
    """
    if legs.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    out_rows: list[dict] = []
    group_cols = ["blockchain", "contract_address", "user_addr"]
    for (blockchain, contract, user), g in legs.groupby(group_cols, sort=False):
        symbol = g["symbol"].iloc[0]
        rows = _compute_user(g, fill_through)
        for r in rows:
            r.update(
                blockchain=blockchain, contract_address=contract,
                symbol=symbol, user_addr=user,
            )
            out_rows.append(r)

    if not out_rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    df = pd.DataFrame(out_rows)
    df = df[df["time_weighted_avg_balance"] > 0].copy()
    df = df.sort_values(
        ["blockchain", "contract_address", "user_addr", "dt", "ref_code"]
    ).reset_index(drop=True)
    return df[OUTPUT_COLUMNS]


def _compute_user(g: pd.DataFrame, fill_through: date) -> list[dict]:
    # --- running_balances: sort by (block, log_index); cumsum + ffill ref ----
    evs = g.sort_values(["block", "log_index"], kind="stable")
    running = 0.0
    cur_ref = SENTINEL
    events: list[dict] = []  # {ts, block, log_index, dt, running, ref}
    for blk, idx, ts, amt, ref in zip(
        evs["block"], evs["log_index"], evs["ts"],
        evs["amount_change"], evs["ref_code"],
    ):
        running += float(amt)
        if pd.notna(ref):
            cur_ref = int(ref)
        events.append({
            "ts": int(ts), "block": int(blk), "log_index": int(idx),
            "dt": _day(int(ts)), "running": running, "ref": cur_ref,
        })

    # --- daily_end_balances: last event (max block/idx) per dt ---------------
    end_bal: dict[date, float] = {}
    end_ref: dict[date, int] = {}
    for e in events:  # events already ordered ascending -> last write wins
        end_bal[e["dt"]] = e["running"]
        end_ref[e["dt"]] = e["ref"]

    tx_days = sorted(end_bal.keys())

    # --- daily_start_balances: lag over TRANSACTION days ---------------------
    start_bal: dict[date, float] = {}
    start_ref: dict[date, int | None] = {}
    prev_bal = 0.0
    prev_ref: int | None = None
    for d in tx_days:
        start_bal[d] = prev_bal
        start_ref[d] = prev_ref
        prev_bal = end_bal[d]
        prev_ref = end_ref[d]

    # --- intra-day segments per (dt, ref_code) -------------------------------
    # segments[dt][ref] = [twab, seg_dur, seg_prod]
    segments: dict[date, dict[int, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))
    events_by_day: dict[date, list[dict]] = defaultdict(list)
    for e in events:
        events_by_day[e["dt"]].append(e)

    for d in tx_days:
        day_events = list(events_by_day[d])
        # synthetic daily_start at midnight (sorts first: block 0, idx -1)
        synthetic = {
            "ts": _midnight_ts(d), "block": 0, "log_index": -1,
            "running": start_bal[d],
            "ref": start_ref[d] if start_ref[d] is not None else SENTINEL,
        }
        seq = [synthetic] + day_events
        seq.sort(key=lambda x: (x["block"], x["log_index"]))
        next_midnight = _midnight_ts(d + timedelta(days=1))
        for i, e in enumerate(seq):
            nxt_ts = seq[i + 1]["ts"] if i + 1 < len(seq) else next_midnight
            dur = nxt_ts - e["ts"]
            if dur == 0:
                continue
            prod = e["running"] * dur
            slot = segments[d][e["ref"]]
            slot[1] += dur          # seg_dur
            slot[2] += prod         # seg_prod
        for ref, slot in segments[d].items():
            slot[0] = slot[2] / SECONDS_PER_DAY  # twab

    # --- user_final_balance + date range -------------------------------------
    final_balance = end_bal[tx_days[-1]]
    first_day = tx_days[0]
    if final_balance > _DUST:
        last_day = max(tx_days[-1], fill_through)
    else:
        last_day = tx_days[-1]

    # --- complete_user_dates + no-transaction-day gap fill --------------------
    out: list[dict] = []
    ff_bal: float | None = None
    ff_ref: int | None = None
    d = first_day
    one = timedelta(days=1)
    while d <= last_day:
        if d in segments:  # transaction day
            for ref, (twab, seg_dur, seg_prod) in segments[d].items():
                out.append({
                    "dt": d, "ref_code": ref,
                    "time_weighted_avg_balance": twab,
                    "day_type": "transaction_day",
                    "segment_duration_seconds": seg_dur,
                    "segment_balance_time_product": seg_prod,
                })
            ff_bal = end_bal[d]
            ff_ref = end_ref[d]
        else:  # no_transaction_day: carry forward last tx day's end state
            out.append({
                "dt": d,
                "ref_code": ff_ref if ff_ref is not None else SENTINEL,
                "time_weighted_avg_balance": ff_bal if ff_bal is not None else 0.0,
                "day_type": "no_transaction_day",
                "segment_duration_seconds": None,
                "segment_balance_time_product": None,
            })
        d += one
    return out
