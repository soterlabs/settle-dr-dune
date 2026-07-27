"""Worker: compute ONE target-chunk's monthly DR and write a CSV.

Chunked because the monolithic run OOMs this 3.7GB box: monthly DR is exactly
additive across disjoint user sets, so per-target runs + client-side combine
produce the identical result (`monthly_dr` is linear in TWA rows; reclass /
rate / conversion are row-local).
"""
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "py"))
from dotenv import load_dotenv
load_dotenv(REPO / ".env")

from drhs import twa  # noqa: E402
from drhs.sources import template_ab, template_c, template_d  # noqa: E402
from drhs.revenue import conversion, deployment, monthly  # noqa: E402

_EXC = template_ab.TEMPLATE_A_EXCLUDED
END = date(2026, 7, 1)
FILL = date(2026, 6, 30)
OUT = REPO / "hypersync-results" / "dr_full"

def _susds_conv():
    return monthly.series_conv(conversion.susds_rates(), "rate")

# chunk -> (family, build_legs thunk, reclassify, conv builder, sp?)
CHUNKS = {
    "stusds":    ("stusds", lambda: template_ab.build_legs([template_ab.STUSDS], end_date=END),
                  monthly.reclass_none, lambda: monthly.series_conv(conversion.stusds_rates(), "rate"), False),
    "farms":     ("farms", lambda: template_d.build_legs(template_d.ALL, end_date=END),
                  monthly.reclass_none, lambda: monthly.const_conv, False),
    "susds_eth": ("susds_susdc", lambda: template_ab.build_legs(
                      [template_ab.SUSDS_ETH], end_date=END, excluded=_EXC,
                      synthetic=(template_ab.COWSWAP,), reroute=template_ab.REROUTED_CODES),
                  monthly.reclass_susds_susdc, _susds_conv, False),
    **{f"susdc_{t.blockchain}": ("susds_susdc",
        (lambda t=t: template_ab.build_legs([t], end_date=END, excluded=_EXC)),
        monthly.reclass_susds_susdc, _susds_conv, False)
       for t in template_ab.TEMPLATE_A_SUSDC},
    **{f"psm3_{t.blockchain}": ("psm3",
        (lambda t=t: template_c.build_legs([t], end_date=END, excluded=_EXC)),
        monthly.reclass_psm3, _susds_conv, False)
       for t in template_c.ALL},
    **{f"sp_{t.symbol}_{t.blockchain}": ("sp",
        (lambda t=t: template_ab.build_legs([t], end_date=END)),
        monthly.reclass_sp, None, True)
       for t in template_ab.TEMPLATE_E},
}

# --- class-D contract-tagged USDS holders (ports of dr_rewards_monthly_usds_*)
# Full USDS balance of one contract attributed to a synthetic code. Mirrors the
# Dune queries: scan from 2024-09-01, XR rate, conversion 1.0, token 'USDS'.
USDS = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"
HOLDER_CHUNKS = {
    "usds_aave_9001": ("usds_aave", "0x32a6268f9ba3642dda7892add74f1d34469a4259", 9001),
    "usds_ref4001":   ("usds_ref4001", "0x1e1d42781fc170ef9da004fb735f56f0276d01b8", 4001),
}

def holder_legs(holder: str, code: int):
    import pandas as pd
    from drhs import events, hypersync
    from datetime import datetime, timezone
    start_ts = int(datetime(2024, 9, 1, tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime(END.year, END.month, END.day, tzinfo=timezone.utc).timestamp())
    fb = hypersync.find_block_at_or_before("ethereum", start_ts)
    tb = hypersync.find_block_at_or_before("ethereum", end_ts - 1)
    ht = events.addr_to_topic(holder)
    rows = hypersync.query_logs("ethereum", [
        {"address": [USDS], "topics": [[events.TRANSFER_TOPIC0], [ht]]},        # out
        {"address": [USDS], "topics": [[events.TRANSFER_TOPIC0], [], [ht]]},    # in
    ], fb, tb).rows
    recs = []
    seen = set()
    for r in rows:
        key = (r.block_number, r.log_index)
        if key in seen or r.block_time >= end_ts:
            continue  # the two selections can overlap on self-transfers
        seen.add(key)
        frm, to = events.topic_to_addr(r.topic1), events.topic_to_addr(r.topic2)
        amt = events.transfer_value(r.data) / 1e18
        delta = (amt if to == holder else 0.0) - (amt if frm == holder else 0.0)
        if delta == 0.0:
            continue
        recs.append({"blockchain": "ethereum", "contract_address": USDS,
                     "symbol": "USDS", "user_addr": holder, "block": r.block_number,
                     "log_index": r.log_index, "ts": r.block_time,
                     "amount_change": delta, "ref_code": code})
    return pd.DataFrame(recs)


def main() -> int:
    name = sys.argv[1]
    if name in HOLDER_CHUNKS:
        # Mirrors dr_rewards_monthly_usds_{aave,ref4001}.sql EXACTLY: daily
        # END-OF-DAY balance snapshots forward-filled over a calendar spine
        # (NOT intraday TWA — the deployed queries snapshot at day end),
        # x XR reward_per / 365, conversion 1.0.
        import pandas as pd
        from datetime import timedelta, datetime, timezone
        from drhs.revenue import rates
        family, holder, code = HOLDER_CHUNKS[name]
        out = OUT / f"chunk_{name}.csv"
        if out.exists():
            print(f"[{name}] exists, skipping")
            return 0
        legs = holder_legs(holder, code)
        print(f"[{name}] {len(legs)} flow events; EOD spine ...", flush=True)
        legs = legs.sort_values(["block", "log_index"])
        legs["bal"] = legs["amount_change"].cumsum()
        legs["dt"] = legs["ts"].map(
            lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc).date())
        eod = legs.groupby("dt")["bal"].last()
        rows, bal = [], 0.0
        d = date(2024, 9, 1)
        while d <= FILL:
            bal = float(eod.get(d, bal))
            rows.append((f"{d.year}-{d.month:02d}-01",
                         bal / 365.0 * rates.daily_rate("XR", d)))
            d += timedelta(days=1)
        m = (pd.DataFrame(rows, columns=["month", "dr_usd"])
             .groupby("month")["dr_usd"].sum().reset_index())
        m["blockchain"], m["token"], m["ref_code"], m["source"] = \
            "ethereum", "USDS", code, family
        m = m[["month", "blockchain", "token", "ref_code", "dr_usd", "source"]]
        m.to_csv(out, index=False)
        print(f"[{name}] wrote {out} ({len(m)} rows)", flush=True)
        return 0
    # optional user-shard "k/N": TWA is per-user independent, so sharding legs
    # by user hash and summing the monthly outputs is exact — needed for
    # psm3_base (3.09M legs), which OOMs this box in one piece.
    shard = sys.argv[2] if len(sys.argv) > 2 else None
    family, build, reclass, conv_builder, is_sp = CHUNKS[name]
    label = name if shard is None else f"{name}_s{shard.replace('/', 'of')}"
    out = OUT / f"chunk_{label}.csv"
    if out.exists():
        print(f"[{label}] exists, skipping")
        return 0
    print(f"[{label}] building legs ...", flush=True)
    legs = build()
    if shard is not None:
        k, n = (int(x) for x in shard.split("/"))
        mask = legs["user_addr"].map(lambda u: int(u[2:10], 16) % n == k)
        legs = legs[mask].copy()
    name = label
    print(f"[{name}] {len(legs)} legs; TWA ...", flush=True)
    tw = twa.compute_twa(legs, fill_through=FILL)
    del legs
    print(f"[{name}] {len(tw)} TWA rows; monthly ...", flush=True)
    if is_sp:
        dep = deployment.deployment_ratios(tw, end=FILL)
        dep_map = {(r.blockchain, r.vault_symbol, r.dt): r.deployment_ratio for r in dep.itertuples()}
        m = monthly.monthly_dr(tw, reclassify=reclass,
                               conv_lookup=monthly.sp_conv(conversion.sp_vault_rates()),
                               sp_deployment=dep_map)
    else:
        m = monthly.monthly_dr(tw, reclassify=reclass, conv_lookup=conv_builder())
    m["source"] = family
    OUT.mkdir(parents=True, exist_ok=True)
    m.to_csv(out, index=False)
    print(f"[{name}] wrote {out} ({len(m)} rows)", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
