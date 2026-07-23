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
   split into correctly attributed segments, never double counted.

3. **Tests**: add cases to `py/tests/test_synthetic.py` (delivery tagged,
   forwarder not tagged, window enforced, cross-program termination). The
   existing `test_multiple_programs_latest_delivery_wins` covers the
   interaction pattern.

4. **Measure the impact** before enabling in production: run the source
   with/without the program restricted to affected wallets (the diff is exact
   because nobody else's TWA can change) and report the per-code deltas —
   see the "Implemented + measured" section of the CowSwap doc for the
   template. This is what ops signs off on (requirement 4).

### B. Executor-mediated aggregator (the 1inch / 0x-Settler shape)

Users receive the token from **per-solver executor contracts** whose addresses
are unbounded; only the tx *entrypoint* (`tx.to`) identifies the program
(1inch: AggregationRouter v6 `0x1111…2a65`, Fusion settlement `0x2d83…aded`).

Not implemented yet. The extension is small and should be built when the
first such program is approved:

- add `entrypoints: frozenset[str]` to `SyntheticProgram`;
- collect program tx hashes with one HyperSync **transaction** query
  (`{"transactions": [{"to": [...]}]}`, fields `hash`,`block_number`) over the
  scan window;
- tag **net-positive recipients** among those txs' transfers — rules 2 and 3
  and all merge/precedence semantics unchanged.

Known gap to accept (or supplement with executor discovery): contract-wallet /
ERC-4337 users don't have `tx.to = router`.

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
