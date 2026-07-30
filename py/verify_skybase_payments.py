"""Verify the reconciliation-v2 sheet's Ethereum txs: USDS transfers, amounts,
recipients, timestamps."""
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

import time
REPO = Path(__file__).resolve().parent.parent
for line in (REPO / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
rpc = os.environ["ETH_RPC"]

USDS = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"
SUSDS = "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
KNOWN = {
    "0x616de58c011f8736fa20c7ae5352f7f6fb9f0669": "cow (1003)",
    "0xfeb4acf3df3cdea7399794d0869ef76a6efaff52": "yearn (1007)",
    "0x6467e807db1e71b9ef04e0e3afb962e4b0900b2b": "defisaver (1002)",
    "0x447bf9d1485abdc4c1778025dfdfbe8b894c3796": "lazysummer timelock (1016)",
}
TXS = [
    ("CC buffer + partner Feb payments (3/4/2026)", "0xfdcf740aa83b602b6c67184e9a220787d3104784faa5134ab42f45428353a1ba"),
    ("MSC 6 Feb (203,134)", "0xbebdd875ef02efe938554d4ea04b5822db0d69bef95c789c27b84b5206b404eb"),
    ("MSC 7 Mar (225,299)", "0xa02c0c6809d65b0f6b1f32c4dd6aa974052b798f02e0fbbb1d3e4d39e98cd5db"),
    ("MSC 8 Apr (201,469)", "0xb03f728b109246f53d8fbc88c6a35584b8df9e2f0281dfca6e6d401adb7230d6"),
    ("MSC 9 May (1,806,616)", "0xa2bffc99b76e5a2e2733ac1f5c350c1d7590e5ae74862fad58b2816b7ab8fba6"),
    ("MSC 10 Jun (204,242)", "0x6edea9580f8cc7f74257ea1e5591137fb772258a1f3a2a569a9f4b51a42a3268"),
    ("DefiSaver true-up 6/30 (58,138)", "0x6b9dcb6e04ddc3c5b98fad38fa291297f7d1be78d09a77491ce9106a61cf90e8"),
]

def call(method, params):
    for attempt in range(6):
        resp = requests.post(rpc, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                        "params": params}, timeout=30)
        try:
            return resp.json().get("result")
        except Exception:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"RPC failing: {resp.status_code} {resp.text[:120]}")

for label, tx in TXS:
    rec = call("eth_getTransactionReceipt", [tx])
    if not rec:
        print(f"\n=== {label}\n  !! TX NOT FOUND: {tx}")
        continue
    blk = call("eth_getBlockByNumber", [rec["blockNumber"], False])
    ts = datetime.fromtimestamp(int(blk["timestamp"], 16), tz=timezone.utc)
    print(f"\n=== {label}\n  tx {tx[:14]}… block {int(rec['blockNumber'],16)} "
          f"@ {ts:%Y-%m-%d %H:%M:%S} UTC status={int(rec['status'],16)}")
    for lg in rec["logs"]:
        if lg["topics"][0].lower() != TRANSFER or len(lg["topics"]) < 3:
            continue
        token = lg["address"].lower()
        tok = {"usds": USDS, "susds": SUSDS}.get("usds") == token and "USDS" or (
            "sUSDS" if token == SUSDS else token[:10])
        tok = "USDS" if token == USDS else ("sUSDS" if token == SUSDS else token[:10])
        frm = "0x" + lg["topics"][1][-40:]
        to = "0x" + lg["topics"][2][-40:]
        amt = int(lg["data"], 16) / 1e18
        if amt < 1:
            continue
        print(f"    {tok:8s} {amt:>14,.2f}  {frm[:10]}… -> {to}  {KNOWN.get(to, '')}")
