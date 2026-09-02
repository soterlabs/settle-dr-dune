# Skybase via 1inch (code 1020): entrypoint-anchored aggregator program

**Date:** 2026-09-02. Branch `osero-venues`. Program
`template_ab.ONEINCH_SKYBASE`, wired on `susds_eth`. Entrypoints (from ops):
1inch AggregationRouter **v4** `0x1111111254fb6c44bac0bed2854e76f90643097d`,
**v5** `0x1111111254eeb25477b68fb85ed929f73a960582`,
**v6** `0x111111125421ca6dc452d289314280a0f8842a65`.

## Why entrypoint anchoring (shape C), not a delivery-contract list

The CowSwap analysis already flagged 1inch as the case shape A cannot cover
([`cowswap-1003-double-attribution.md`](cowswap-1003-double-attribution.md)
§"Extending", 4011 row): deliveries come from **per-solver executors**, not
from the router. Measured on the full sUSDS-eth history (2024-09 → 2026-09-02,
391,828 sUSDS txs, every `Transfer` joined to its `tx.to`):

| | txs |
|---|---|
| sUSDS txs whose `tx.to` is a 1inch router | **4,025** (v6 3,868 · v5 157 · v4 0) |
| …with a router → net-positive-recipient edge | 435 (11 %) |
| …without — sUSDS reaches the user from an executor / pool | **3,590 (89 %)** |
| distinct net-positive recipients | 222 (187 via a direct edge) |

Top senders to the recipient in the no-direct txs are 1inch executors
(`0x5141b82f…` 1,041, `0x990636ec…` 735, `0x8c864d0c…` 336, `0x0bb7a4fd…`
229, `0x7f51c134…` 210, `0x4c3ccc98…` 200). A fixed delivery set would miss
89 % of the program; only the **tx entrypoint** identifies it.

Volume by month: 2–50 (Dec 2024–Jan 2025), **200–350 / month** Feb 2025 →
Jun 2026, then 28 (Jul 2026), 31 (Aug 2026).

## Mechanism

`EntrypointProgram(name, ref_code, entrypoints, start, end)` in
`template_ab`. At fetch time the target's `Transfer` rows are pulled **with the
transaction join** (`hypersync.query_logs(..., with_tx_to=True)` fills
`LogRow.tx_to` — same scan, slightly more payload, no second pass); the
program resolves to a `SyntheticProgram` with `txs = {tx : tx.to ∈ entrypoints}`
and an **empty** `contracts` set, which `synthetic_referrals` reads as: inside
an anchored tx, **every incoming transfer — mints included — to a wallet that
ends the tx net-positive** is a delivery. Forwarders (executors, the router
itself, pools that only pass through) net to ≤ 0 and are never tagged; a pool
that *retains* is tagged (the accepted pooled-holder relabel). Precedence,
window and last-wins termination are the shared rules.

Fixtures carry no `tx_to` (defaults to `None`) → entrypoint programs resolve
to an empty tx set offline, so Dune-parity replays are untouched.

## Interaction with 1inch's own code 4011

4011 is 1inch's partner code: its executors emit `Referral(4011)` and the code
is re-routed to the end recipient (`REROUTED_CODES`). Re-routed real codes
rank **above** pseudo-tags (the rule the settled months were paid under), so:

- a 1inch-router tx that also carries `Referral(4011)` → user gets **4011**;
- every other 1inch-router tx → user gets **1020**.

Overlap is small: of 506 `Referral(4011)` txs only **24** enter via these
routers (the rest are Fusion settlement `0x2d83…aded`). If Skybase should win
in those 24 as well, that is an explicit precedence change for ops — not made
here.

## Eligibility window — PROVISIONAL

`start = 2026-09-01` (the first unsettled month). Enabling the program from
history would re-attribute settled Skybase/untagged months (1inch volume runs
back to Dec 2024); the full-history measurement below is provided so ops can
choose a start date knowingly — moving it is one constant.

## Known gaps

- **Contract-wallet / ERC-4337 users** of the 1inch frontend: `tx.to` is the
  EntryPoint (`0x0000000071727de2…`), not the router. 45 such txs observed with
  a router sub-call — invisible to this rule (the playbook's known gap).
- Other frontends calling 1inch routers as a sub-call (CowSwap settlement 94
  txs, MetaMask swap router 38, …) are correctly **not** 1020 — their own
  programs own them.
- Wired on **sUSDS-eth and every sUSDC source** (eth + base / arbitrum /
  optimism / unichain): the 1inch routers are deployed at the same address on
  each chain, so the one `EntrypointProgram` resolves per target. L2 sUSDS via
  PSM3 (Template C) would need the analogous `tx_to` hook — not done.

## Impact (full history, measured)

Method as for CowSwap 1003 / Osero 3006: the program enabled for the FULL
history (no eligibility start) vs the production baseline (CowSwap 1003 +
re-routed 1004/4011), restricted to the 223 wallets the program tags (3,415
tags in 4,022 router txs); only their TWA can change, so the diff is exact.
`hypersync-results/measurements/oneinch_1020_delta.csv`.

| month | 1020 (USD) | month | 1020 (USD) |
|---|---|---|---|
| 2025-01 | **33,321.86** | 2025-12 | 7,334.88 |
| 2025-02 | 10,295.97 | 2026-01 | 3,829.32 |
| 2025-03 | 2,049.33 | 2026-02 | 3,455.94 |
| 2025-04 | 2,023.45 | 2026-03 | 6,125.37 |
| 2025-05 | 1,126.33 | 2026-04 | 5,591.47 |
| 2025-06 | 6,450.27 | 2026-05 | 3,491.96 |
| 2025-07 | 8,371.47 | 2026-06 | 1,898.93 |
| 2025-08 | 7,888.89 | 2026-07 | 441.79 |
| 2025-09 | 6,550.95 | 2026-08 | 377.39 |
| 2025-10 | 5,558.00 | | |
| 2025-11 | 3,111.36 | **total** | **119,294.92** |

Displaced (net delta = **$0.00**):

| code | delta (USD) | share |
|---|---|---|
| 1003 CowSwap | **−61,975.08** | 52 % |
| 99 untagged | −47,055.44 | 39 % |
| 1004 Paraswap | −6,087.27 | 5 % |
| 0 | −1,793.01 | |
| 1 | −1,529.85 | |
| 4011 1inch | −405.62 | |
| 128 Spark.lend | −366.38 | |
| 1002 DeFiSaver | −82.27 | |

Read before choosing a start date:

- **Half of the program comes out of CowSwap 1003, not out of untagged.** The
  stream is last-referral-wins on the *whole* balance: a wallet tagged 1003 by
  a CowSwap buy that later swaps via 1inch is re-tagged 1020 in full, and vice
  versa. That is the existing global convention (FIFO vs last-wins,
  `dr-review-checklist.md`), not something this program introduces — but 1inch
  and CowSwap share many users, so here it moves real money between two paid
  programs.
- **Jan 2025 ($33k) is dominated by a single large wallet**; the steady-state
  is $2–8k/month through mid-2026 and ~$400/month in Jul–Aug 2026 as 1inch
  sUSDS volume fell to ~30 txs/month.
- At the provisional `start = 2026-09-01` nothing settled moves; backdating to
  any month re-attributes every month from there, including paid CowSwap
  months.
