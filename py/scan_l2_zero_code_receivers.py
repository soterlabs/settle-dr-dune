import os, sys
from pathlib import Path
from datetime import datetime, timezone
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
REPO = Path(__file__).resolve().parent.parent
for line in (REPO / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
from drhs import events, hypersync
from drhs.sources import template_c
from drhs.revenue.monthly import PSM3_CODE0_10001
from collections import defaultdict

LABELS = {
 "0x2917956eff0b5eaf030abdb4ef4296df775009ca": "ALM (base)",
 "0x3128a0f7f0ea68e7b7c9b00afa7e41045828e858": "sUSDC vault (base)",
 "0x1601843c5e9bc251a3272907010afa41fa18347e": "PSM3 (base)",
 "0x2c776041ccfe903071af44aa147368a9c8eea518": "compound USDS (base)",
 "0xc3bef21ea7deb5c34cf33e918c8e28972c8048ed": "parallel protocol (base)",
 "0x1647d5950dee7332f748b5d02ff4abe7ddcaff6b": "(base #6)",
 "0x876664f0c9ff24d1aa355ce9f1680ae1a5bf36fb": "ALM (optimism)",
 "0xcf9326e24ebffbef22ce1050007a43a3c0b6db55": "sUSDC vault (optimism)",
 "0xe0f9978b907853f354d79188a3defbd41978af62": "PSM3 (optimism)",
 "0x345e368fccd62266b3f5f37c9a131fd1c39f5869": "ALM (unichain)",
 "0x14d9143becc348920b68d123687045db49a016c6": "sUSDC vault (unichain)",
 "0x7b42ed932f26509465f7ce3faf76ffce1275312f": "PSM3 (unichain)",
}
start_ts = int(datetime(2024, 9, 1, tzinfo=timezone.utc).timestamp())
for t in (template_c.OPT, template_c.UNI, template_c.BASE):   # fast chains first
    chain = t.blockchain
    try:
        fb = hypersync.find_block_at_or_before(chain, start_ts)
    except Exception:
        fb = 0
    head = hypersync.archive_height(chain) - 10
    rows = hypersync.query_logs(chain,
        [{"address": [t.psm3_addr.lower()],
          "topics": [[events.SWAP_TOPIC0], [], [events.addr_to_topic(t.address)]]}],
        fb, head).rows
    infra = PSM3_CODE0_10001[chain]
    vol_infra = defaultdict(float)
    n_user, vol_user, users = 0, 0.0, set()
    for r in rows:
        if events.swap_referral_code(r.data) != 0:
            continue
        rcv = events.topic_to_addr(r.topic3)
        h = r.data.removeprefix("0x")
        words = [int(h[i*64:(i+1)*64], 16) for i in range(len(h)//64)]
        amt = words[-2] / 1e18
        if rcv in infra:
            vol_infra[rcv] += amt
        else:
            n_user += 1; vol_user += amt; users.add(rcv)
    print(f"\n== {chain}: {len(rows)} sUSDS-out swaps", flush=True)
    print("  -> 10001 receivers (cumulative sUSDS bought at code 0):", flush=True)
    for a, v in sorted(vol_infra.items(), key=lambda kv: -kv[1]):
        print(f"     {LABELS.get(a, a):28s} {v:>15,.0f}", flush=True)
    print(f"  -> 10000: {n_user} swaps, {len(users)} distinct wallets, {vol_user:,.0f} sUSDS", flush=True)
