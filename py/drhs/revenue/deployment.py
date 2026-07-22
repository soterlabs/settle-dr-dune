"""sp* vault deployment ratio per (blockchain, vault_symbol, day) — Layer 3b.

Port of deployment_ratio_sp.sql (Dune 7877551). Fully event-derived:
  * vault_idle_holdings = daily TWA of the UNDERLYING token (USDC/USDT/PYUSD)
    held BY the vault address, from that token's ERC20 Transfer events in/out of
    the vault (query_6619793 logic).
  * vault_total_supply = sp* share TWA (template E output) summed to vault level,
    spETH excluded.
  * deployment_ratio = greatest((total - idle) / total, 0).

SCOPE (mirrors the SQL + monthly note): the computed ratio is used ONLY for
spUSDC. spUSDT/spPYUSD are forced to ratio 1.0 (they hold their underlying
directly; the idle model is meaningless for them); spETH is excluded (its DR is
zeroed). The idle TWA differs from the user-level engine in two ways: divide by
covered seconds (== 86400 for tx days, which always have a midnight-start event)
and the calendar ALWAYS extends to 2026-06-30 (the vault always exists — no
balance-trim).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from .. import events, hypersync

END_CAP = date(2026, 6, 30)
SECONDS_PER_DAY = 86400

# (blockchain, vault_symbol, vault_addr, underlying_addr, underlying_decimals, start_date)
VAULT_TOKENS = [
    ("ethereum", "spUSDC", "0x28b3a8fb53b741a8fd78c0fb9a6b2393d896a43d",
     "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", 6, date(2025, 10, 1)),
    ("ethereum", "spUSDT", "0xe2e7a17dff93280dec073c995595155283e3c372",
     "0xdac17f958d2ee523a2206206994597c13d831ec7", 6, date(2025, 10, 1)),
    ("ethereum", "spPYUSD", "0x80128dbb9f07b93dde62a6daeadb69ed14a7d354",
     "0x6c3ea9036406852006290770bedfcaba0e23a0e8", 6, date(2025, 12, 1)),
    ("avalanche_c", "spUSDC", "0x28b3a8fb53b741a8fd78c0fb9a6b2393d896a43d",
     "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e", 6, date(2025, 10, 8)),
]
_FORCED_ONE = {"spUSDT", "spPYUSD"}


def _midnight(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def idle_twa_series(chain, vault_addr, underlying_addr, decimals, start, end) -> dict[date, float]:
    """Daily TWA of the underlying token balance held by ``vault_addr``.

    Single series (no ref, no balance-trim): calendar first_tx..end, tx-day TWA
    = Σ(bal·dur)/86400, no-transaction days forward-fill the last end balance.
    """
    vault_topic = events.addr_to_topic(vault_addr)
    start_ts = _midnight(start)
    end_plus = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)
    try:
        from_block = hypersync.find_block_at_or_before(chain, start_ts)
    except hypersync.HyperSyncError:
        from_block = 0
    to_block = hypersync.find_block_at_or_before(chain, int(end_plus.timestamp()))
    sel = [
        {"address": [underlying_addr.lower()], "topics": [[events.TRANSFER_TOPIC0], [vault_topic]]},       # from vault
        {"address": [underlying_addr.lower()], "topics": [[events.TRANSFER_TOPIC0], [], [vault_topic]]},   # to vault
    ]
    rows = hypersync.query_logs(chain, sel, from_block, to_block).rows
    scale = 10 ** decimals

    # dedup (a vault->vault transfer would match both selections) + build legs
    seen: set[tuple[int, int]] = set()
    legs: list[tuple[int, int, int, float]] = []  # (block, log_index, ts, amount)
    for r in rows:
        k = (r.block_number, r.log_index)
        if k in seen:
            continue
        seen.add(k)
        d = datetime.fromtimestamp(r.block_time, tz=timezone.utc).date()
        if d < start or d > end:
            continue
        frm = events.topic_to_addr(r.topic1)
        to = events.topic_to_addr(r.topic2)
        val = events.transfer_value(r.data) / scale
        amt = (val if to == vault_addr.lower() else 0.0) - (val if frm == vault_addr.lower() else 0.0)
        if amt != 0.0:
            legs.append((r.block_number, r.log_index, r.block_time, amt))
    if not legs:
        return {}

    legs.sort(key=lambda x: (x[0], x[1]))
    running = 0.0
    events_by_day: dict[date, list[tuple[int, float]]] = defaultdict(list)  # dt -> [(ts, running)]
    end_bal: dict[date, float] = {}
    for blk, idx, ts, amt in legs:
        running += amt
        d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        events_by_day[d].append((ts, running))
        end_bal[d] = running  # ascending -> last wins

    tx_days = sorted(end_bal)
    # start-of-day balance = previous tx day's end (lag, default 0)
    start_bal: dict[date, float] = {}
    prev = 0.0
    for d in tx_days:
        start_bal[d] = prev
        prev = end_bal[d]

    twa_tx: dict[date, float] = {}
    for d in tx_days:
        seq = [(_midnight(d), start_bal[d])] + events_by_day[d]  # midnight start event
        seq.sort(key=lambda x: x[0])
        nxt_mid = _midnight(d + timedelta(days=1))
        prod = 0.0
        for i, (ts, bal) in enumerate(seq):
            nt = seq[i + 1][0] if i + 1 < len(seq) else nxt_mid
            prod += bal * (nt - ts)
        twa_tx[d] = prod / SECONDS_PER_DAY

    # calendar first_tx .. end, forward-fill end_bal on no-transaction days
    out: dict[date, float] = {}
    ff: float | None = None
    d = tx_days[0]
    one = timedelta(days=1)
    while d <= end:
        if d in twa_tx:
            out[d] = twa_tx[d]
            ff = end_bal[d]
        else:
            out[d] = ff if ff is not None else 0.0
        d += one
    return out


def deployment_ratios(sp_twa: pd.DataFrame, end: date = END_CAP) -> pd.DataFrame:
    """[blockchain, vault_symbol, dt, vault_total_supply, vault_idle_holdings,
    vault_deployed, deployment_ratio] from the sp* share TWA + idle series."""
    end = min(end, END_CAP)
    totals = (sp_twa[sp_twa["symbol"] != "spETH"]
              .groupby(["blockchain", "symbol", "dt"])["time_weighted_avg_balance"].sum()
              .reset_index()
              .rename(columns={"symbol": "vault_symbol", "time_weighted_avg_balance": "vault_total_supply"}))
    totals["dt"] = totals["dt"].astype(str).str[:10]

    idle_map: dict[tuple[str, str, str], float] = {}
    for chain, sym, vault, underlying, dec, start in VAULT_TOKENS:
        if sym in _FORCED_ONE:
            continue  # idle ignored for these; ratio forced to 1
        series = idle_twa_series(chain, vault, underlying, dec, start, end)
        for d, v in series.items():
            idle_map[(chain, sym, d.isoformat())] = v

    rows = []
    for t in totals.itertuples():
        total = float(t.vault_total_supply)
        if t.vault_symbol in _FORCED_ONE:
            idle, deployed, ratio = 0.0, total, 1.0
        else:
            idle = idle_map.get((t.blockchain, t.vault_symbol, t.dt), 0.0)
            deployed = total - idle
            ratio = max(deployed / total, 0.0) if total > 0 else 0.0
        rows.append({
            "blockchain": t.blockchain, "vault_symbol": t.vault_symbol, "dt": t.dt,
            "vault_total_supply": total, "vault_idle_holdings": idle,
            "vault_deployed": deployed, "deployment_ratio": ratio,
        })
    return pd.DataFrame(rows)
