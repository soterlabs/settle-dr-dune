"""Memory-lean psm3_base chunk: fetch once, stream transfers into compact
per-shard column arrays (no dict-per-leg), then TWA shard by shard.

Replicates template_ab.transfer_legs semantics exactly (window filter, zero-addr
skip, decimal scale, (tx,user) ref lookup) + the excluded-user filter that
build_legs applies. Output: chunk_psm3_base_s{k}of{N}.csv, additive with the
other chunks.
"""
import gc
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "py"))
from dotenv import load_dotenv
load_dotenv(REPO / ".env")

import pandas as pd  # noqa: E402
from drhs import events, twa  # noqa: E402
from drhs.sources import template_ab, template_c  # noqa: E402
from drhs.revenue import conversion, monthly  # noqa: E402

N = 8
T = template_c.BASE
END = date(2026, 7, 1)
FILL = date(2026, 6, 30)
OUT = REPO / "hypersync-results" / "dr_full"
EXC = template_ab.TEMPLATE_A_EXCLUDED

end_ts = template_c._end_ts(END)
print("fetching rows ...", flush=True)
swap_rows, tr_rows = template_c.fetch_target_rows(T, end_ts)
print(f"swaps={len(swap_rows)} transfers={len(tr_rows)}", flush=True)
latest = template_c.latest_referral_from_swaps(T, swap_rows)
del swap_rows
gc.collect()

scale = 10 ** T.decimals
start_day = T.start_date
shards = [{"user_addr": [], "block": [], "log_index": [], "ts": [],
           "amount_change": [], "ref_code": []} for _ in range(N)]

def add(user, r, amt, tx):
    if user == events.ZERO_ADDR or user in EXC:
        return
    s = shards[int(user[2:10], 16) % N]
    ref = latest.get((tx, user))
    s["user_addr"].append(user)
    s["block"].append(r.block_number)
    s["log_index"].append(r.log_index)
    s["ts"].append(r.block_time)
    s["amount_change"].append(amt)
    s["ref_code"].append(ref[1] if ref is not None else None)

kept = 0
for r in tr_rows:
    if r.block_time >= end_ts:
        continue
    if datetime.fromtimestamp(r.block_time, tz=timezone.utc).date() < start_day:
        continue
    amt = events.transfer_value(r.data) / scale
    tx = r.transaction_hash
    add(events.topic_to_addr(r.topic2), r, amt, tx)
    add(events.topic_to_addr(r.topic1), r, -amt, tx)
    kept += 1
print(f"streamed {kept} transfers into {N} shards", flush=True)
del tr_rows, latest
gc.collect()

conv = monthly.series_conv(conversion.susds_rates(), "rate")
for k in range(N):
    out = OUT / f"chunk_psm3_base_s{k}of{N}.csv"
    if out.exists():
        print(f"[s{k}] exists, skipping", flush=True)
        shards[k] = None
        continue
    s = shards[k]
    legs = pd.DataFrame({
        "blockchain": T.blockchain, "contract_address": T.address.lower(),
        "symbol": T.symbol, "user_addr": s["user_addr"], "block": s["block"],
        "log_index": s["log_index"], "ts": s["ts"],
        "amount_change": s["amount_change"],
        "ref_code": pd.array(s["ref_code"], dtype="Int64"),
    })
    shards[k] = None
    del s
    gc.collect()
    print(f"[s{k}] {len(legs)} legs; TWA ...", flush=True)
    tw = twa.compute_twa(legs, fill_through=FILL)
    del legs
    gc.collect()
    print(f"[s{k}] {len(tw)} TWA rows; monthly ...", flush=True)
    m = monthly.monthly_dr(tw, reclassify=monthly.reclass_psm3, conv_lookup=conv)
    del tw
    gc.collect()
    m["source"] = "psm3"
    m.to_csv(out, index=False)
    print(f"[s{k}] wrote {out} ({len(m)} rows)", flush=True)
print("psm3_base lean done", flush=True)
