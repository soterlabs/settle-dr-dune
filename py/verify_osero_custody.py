"""Audit: the Osero gtSkyLooping custody perimeter vs live chain state.

Builds the strategy's legs through the REAL pipeline path (template_ab
legs_from_rows with custody rows, i.e. exactly what the susds_eth source
does) restricted to the strategy's own events, then asserts the spec's §7
invariant: tracked balance == sUSDS.balanceOf(strategy) + sum of the
strategy's Morpho collateral positions (all sUSDS-collateral markets).

    .venv/bin/python py/verify_osero_custody.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "py"))
from dotenv import load_dotenv
load_dotenv(REPO / ".env")
import os  # noqa: E402

# ETH_RPC lives in the sibling settlement-cycle checkout on the ops box; the
# repo .env only carries the HyperSync token. Same pattern as
# verify_skybase_payments.py.
_SIBLING_ENV = REPO.parent / "settlement-cycle" / ".env"
if "ETH_RPC" not in os.environ and _SIBLING_ENV.exists():
    load_dotenv(_SIBLING_ENV)

import requests  # noqa: E402

from drhs import events, hypersync  # noqa: E402
from drhs.sources import custody, template_ab  # noqa: E402

T = template_ab.SUSDS_ETH
P = custody.OSERO_GTSKYLOOPING


def main() -> int:
    now = int(datetime.now(tz=timezone.utc).timestamp())
    fb = hypersync.find_block_at_or_before(
        P.blockchain, int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()))
    head = hypersync.archive_height(P.blockchain) - 10
    st = events.addr_to_topic(P.strategy)

    ref_rows = hypersync.query_logs(P.blockchain,
        [{"address": [T.address], "topics": [[events.REFERRAL_TOPIC0], [], [st]]}],
        fb, head).rows
    tr_rows = hypersync.query_logs(P.blockchain,
        [{"address": [T.address], "topics": [[events.TRANSFER_TOPIC0], [st]]},
         {"address": [T.address], "topics": [[events.TRANSFER_TOPIC0], [], [st]]}],
        fb, head).rows
    seen: set[tuple[int, int]] = set()
    tr_rows = [r for r in tr_rows
               if (k := (r.block_number, r.log_index)) not in seen and not seen.add(k)]
    pos_rows = custody.fetch_position_rows(P, fb, head)
    print(f"referrals={len(ref_rows)} transfers={len(tr_rows)} position events={len(pos_rows)}")

    legs = template_ab.legs_from_rows(T, ref_rows, tr_rows, now,
                                      custody_rows=[(P, pos_rows)])
    mine = legs[legs["user_addr"] == P.strategy]
    tracked = float(mine["amount_change"].sum())
    tagged = sorted({int(r) for r in mine["ref_code"].dropna().unique()})
    print(f"tracked balance (pipeline path): {tracked:,.6f} sUSDS | codes on legs: {tagged}")

    rpc = os.environ.get("ETH_RPC")
    if not rpc:
        raise SystemExit("ETH_RPC not set (repo .env or ../settlement-cycle/.env) — "
                         "needed for the live balanceOf/position reads")
    def call(to, data):
        return requests.post(rpc, json={"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                                        "params": [{"to": to, "data": data}, "latest"]},
                             timeout=30).json()["result"]
    w = int(call(T.address, "0x70a08231" + "0" * 24 + P.strategy[2:]), 16) / 1e18
    c = 0.0
    for mkt in sorted({r.topic1 for r in pos_rows}):
        pos = call(custody.MORPHO_BLUE, "0x93c52062" + mkt[2:] + "0" * 24 + P.strategy[2:])
        c += int(pos[2 + 128:2 + 192], 16) / 1e18
    print(f"live: W={w:,.6f} + C={c:,.6f} = {w + c:,.6f}")
    diff = abs(tracked - (w + c))
    print(f"|tracked - (W+C)| = {diff:.6f} -> {'OK' if diff < 1e-6 else 'MISMATCH'}")
    return 0 if diff < 1e-6 else 1


if __name__ == "__main__":
    raise SystemExit(main())
