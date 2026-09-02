# Adding a new aggregator program (synthetic ref code)

How to add DR tracking for an aggregator (Paraswap, 1inch, 0x, Enso, …) the
way CowSwap 1003 is tracked. Background and the double-counting analysis that
motivates this design: [`cowswap-1003-double-attribution.md`](cowswap-1003-double-attribution.md).

## The model

Aggregators never emit a user-level `Referral` event — when their contracts
emit one at all, it lands on the **router itself** (all 926 sUSDS
`Referral(1004)` events sit on Paraswap routers; all 4011 events on 1inch
executors). End users just receive the token as a plain `Transfer` out of the
aggregator's delivery contract.

A `SyntheticProgram` (`py/drhs/sources/template_ab.py`) turns those deliveries
into **pseudo-referral legs inside the same attribution stream** as real
`Referral` events. This is the invariant that keeps the system correct:

- the tag **relabels** the wallet's balance segments (out of untagged-99 or a
  prior code) — it never adds a second copy, so total DR is unchanged and no
  balance is ever counted under two codes (Atlas A.2.2.8.1.2.1.2.2.4);
- **any later attribution signal ends the tag** — a real Referral, a PSM3
  code, or another program's delivery (last-wins forward-fill in the TWA
  engine);
- a real `Referral` for the same `(tx, wallet)` beats a pseudo-referral.

Tagging rule per delivery tx: wallet W gets the program's code iff

1. W received the token **from** one of the program's contracts in that tx, and
2. W's **net token delta across the whole tx is positive**, and
3. the tx falls inside the program's eligibility window.

Rule 2 is the intermediary guard: solvers, routers, and the settlement itself
only *forward* within a tx (net ≈ 0) and are never tagged. Without it, the
naive "deposit owner in an aggregator tx" signal tags exclusively
intermediaries — 46/46 contracts, zero end users, in the CowSwap audit.

## What you need before writing code

| # | Requirement | Who provides it |
|---|---|---|
| 1 | **Synthetic ref code**, chosen to collide with nothing (cf. `ref_code_0_sources.md`) | Sky / ops |
| 2 | **Delivery contract addresses**, per chain, per version era (routers get redeployed — list every era that overlaps the settlement window) | you, verified on-chain |
| 3 | **Eligibility window** (program start / termination dates — Atlas requires them) | Sky / ops |
| 4 | **Payout-policy sign-off.** Enabling a program *shifts* DR from 99/untagged (and, going forward, from prior partner codes) to the aggregator. Total unchanged, but who gets paid changes — product decision, never an engineering default | Sky / ops |
| 5 | **A reference series** to sanity-check against (e.g. Amatsu's "cow" monthly payouts for 1003), if the program should reproduce an existing stream | partner / Amatsu |

## Implementation steps

### A. Fixed-delivery aggregator (the CowSwap / Paraswap shape)

Users receive the token directly from a small fixed set of contracts.
This is a registry entry — no new code:

1. **Declare the program** in `py/drhs/sources/template_ab.py`:

   ```python
   PARASWAP = SyntheticProgram(
       "paraswap", 1004,
       frozenset({
           "0xdef171fe48cf0115b1d80b88dc8eab59176fee57",  # Augustus v5
           "0x6a000f20005980200259b80c5102003040001068",  # Augustus v6.2
           "0x00c600b30fb0400701010f4b080409018b9006e0",  # Augustus v6.1
           "0x000010036c0190e009a000d0fc3541100a07380a",  # Paraswap Delta
       }),
       start=date(2025, 7, 1),     # eligibility window from ops
   )
   ```

2. **Wire it** in `py/run_source.py`:

   ```python
   "susds_eth": SourceSpec(..., synthetic=(template_ab.COWSWAP, template_ab.PARASWAP)),
   ```

   Programs in one source terminate each other automatically (last delivery
   wins), so overlap wallets (48 CowSwap∩Paraswap wallets in the audit) are
   split into correctly attributed segments, never double counted. Two
   programs anchored on the **same** delivery contract in the **same** tx tie
   on `log_index`; the tie goes to the **last program in the tuple**.

3. **Tests**: add cases to `py/tests/test_synthetic.py` (delivery tagged,
   forwarder not tagged, window enforced, cross-program termination). The
   existing `test_multiple_programs_latest_delivery_wins` covers the
   interaction pattern.

4. **Measure the impact** before enabling in production: run the source
   with/without the program restricted to affected wallets (the diff is exact
   because nobody else's TWA can change) and report the per-code deltas —
   see the "Implemented + measured" section of the CowSwap doc for the
   template. This is what ops signs off on (requirement 4).

### B. Referral-emitting aggregator (the Paraswap / 1inch shape) — IMPLEMENTED

These aggregators deposit into the vault themselves and pass their partner
code, so a **real `Referral(code)` event fires — owned by the
router/executor**, not the end user (all 926 `Referral(1004)` events land on
Paraswap routers; all 426 `Referral(4011)` events on 1inch executors). The
mechanism (`template_ab.rerouted_referrals`, allowlist
`template_ab.REROUTED_CODES`): when an allowlisted code lands on owner O in
tx T and O is a **net-zero/negative forwarder** in T, the code is re-attached
to the net-positive recipients of transfers *from O* in T. If O is itself
net-positive — a partner vault holding for its users, like Yearn's 1007
contracts (16.8M sUSDS retained) — nothing is re-routed.

Adding such a partner = **adding its code to `REROUTED_CODES`**. No address
registry: the tag is anchored to the emitting intermediary itself, so router
redeployments are picked up automatically. Precedence per (tx, user): real
user Referral > re-routed code > delivery pseudo-tag. This is the corrected
descendant of the removed `referral_per_tx_fallback` CTE.

**Hops.** The default re-route follows the *direct* owner → recipient edge
only; a recipient that itself forwards on (net ≤ 0 in the tx) is a hop and is
not tagged. Some integrations forward twice — Jumper Earn's Sky deposit adapter
mints with 3006, hands the shares to the LiFiDiamond, and the Diamond delivers
to the user — so nothing would be re-routed. For those, add the code to
`template_ab.REROUTE_FOLLOW_HOPS` as well: the walk then continues through
net-zero forwarders to the first net-positive recipients — following only
transfers that happen **after** the hop received the shares, so an unrelated
earlier delivery out of a shared router never inherits the code. It is opt-in
per code on purpose — 1004 / 4011 were settled under the direct rule and must stay
byte-identical. See [`osero-codes.md`](osero-codes.md).

Wire `reroute=template_ab.REROUTED_CODES` on **every source whose token the
partner deposits into** — `susds_eth` alone missed 58 of 65 Jumper events,
which were on sUSDC L2s.

### C. Aggregator without a fixed delivery contract (1inch / 0x Settler / Enso shape) — IMPLEMENTED

Delivery comes from rotating per-solver executors or straight from pools
(1inch on sUSDS-eth: only 11 % of router txs have a router → user edge), so
shape A cannot see it. Only the tx *entrypoint* (`tx.to`) identifies the
program. `template_ab.EntrypointProgram(name, code, entrypoints, start, end)`:

- the program fetches **its own** `Transfer` rows with the transaction join
  (`query_logs(..., with_tx_to=True)` → `LogRow.tx_to`) over its eligibility
  window only, and resolves to `txs = {tx : tx.to ∈ entrypoints}`. The
  pipeline's full Transfer stream is left alone on purpose: joining it would
  re-key the largest cache entries and maintain a second copy forever for rows
  the window can never tag;
- inside those txs, **every incoming transfer (mints included) to a
  net-positive wallet** is a delivery; forwarders net to ≤ 0 and are skipped —
  all other rules and precedence unchanged.

First use: Skybase's 1inch program 1020, [`oneinch-1020-skybase.md`](oneinch-1020-skybase.md).
Known gap: contract-wallet / ERC-4337 users have `tx.to = EntryPoint`, not the
router (45 such txs observed).

### D. Multi-tenant router with an integrator id (Li.Fi shape) — IMPLEMENTED

One delivery contract shared by many integrators: tagging all its deliveries
would hand the program other frontends' users. Anchor on the router's own
events that carry the integrator id — `drhs/sources/lifi.py`
(`IntegratorProgram`), same-chain + cross-chain joined on `transactionId` —
and restrict an ordinary `SyntheticProgram` to those txs (`txs`).
[`lifi-oserofrontend.md`](lifi-oserofrontend.md).

## Sanity checklist before enabling

- [ ] Delivery contracts verified on-chain (sample txs: token really flows
      contract → end user; run the recon snippets in the CowSwap doc).
- [ ] No delivery contract is itself a tracked venue or an excluded pooled
      holder (`TEMPLATE_A_EXCLUDED`); if a **tagged recipient** turns out to
      be a pooled contract (audit found Uniswap v4 PoolManager, Balancer V3
      vault), decide: exclude it or accept that its (already-counted-once)
      balance is relabeled to the program.
- [ ] `validate.py` / `validate_monthly.py` still pass — they compare the
      **pre-synthetic** path (`include_synthetic=False`) against Dune, which
      carries no synthetic programs; parity fixtures likewise.
- [ ] Impact numbers (per-code deltas, net = $0) reviewed by ops.
- [ ] Never add a standalone per-program query to the combine step — that
      reintroduces double counting by construction.

## Scope notes

- Phase 1 covers **sUSDS-eth** (Template A). Applying a program to other
  tokens (stUSDS, sUSDC) is the same registry entry on that target's source;
  PSM3-L2 sUSDS needs the analogous hook in Template C (swap inside a program
  tx). Amatsu attributes 1003 across sUSDS + stUSDS + USDS farms — farm
  coverage additionally requires a cross-venue tag-propagation decision
  (buy USDS via aggregator, then stake) that is methodology, not code.
- FIFO vs last-referral-wins is a global convention question
  (`dr-review-checklist.md` §on FIFO); synthetic programs inherit whatever
  the stream does.
