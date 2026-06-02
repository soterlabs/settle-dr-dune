# Settlement-Handover DR Repo Review

## Purpose

This document records what we learn from reviewing `settlement-handover/` — a repo built by another team that purports to perform Distribution Rewards (DR) calculations — before we proceed with the roadmap in `dr-query-analysis.md`. It addresses three questions:

1. **What pieces of DR can it actually compute today?** (Including: can it run at all? Is configuration missing?)
2. **What does its methodology imply** — and how does it confirm or conflict with the open questions in `dr-query-analysis.md`?
3. **What is the scope of its calculations**, and how does it compare in depth and breadth to the Spark Dune queries we are basing our project on?

The "DR-relevant" portion of the repo is `settlement-handover/src/update-distribution-rewards-calculations/` plus its `calculations/` subdirectory. The rest of `settlement-handover/src/` (debt-pnl, idle stablecoins, PSM3, etc.) is unrelated to DR and out of scope here.

---

## 1. What it can compute right now

### 1.1 Out of the box: nothing

The repo is a Deno HTTP service exposing one endpoint (`GET /api/update-distribution-rewards`) that drives the DR calculation pipeline. As checked into the workspace, **the pipeline cannot run**. Specifically:

| Missing piece | Where | Effect |
|---|---|---|
| Database connection | `settlement-handover/src/db/db.ts` | Both `fullAccess` and `readOnly` Kysely pools are stubbed to `null`. Any query throws on first `.executeTakeFirst()`. |
| `DATABASE_URL` | `settlement-handover/.env` | Empty string. Nothing to wire even if `db.ts` were filled in. |
| `RPC_KEY` | `settlement-handover/.env` | Empty. Used by `fetchTokenPrices.ts` to hit Alchemy historical-prices API at `https://api.g.alchemy.com/prices/v1/${RPC_KEY}/tokens/historical`. Without it, all USD conversions for sUSDS / sUSDC / sp* fall back silently to empty arrays (and downstream `getWeightedAveragePricePrecise` reduces to the raw time-delta — no rate weighting, no USD figure). |
| Postgres driver | not present | `deno.json` imports `kysely` only; no `kysely-postgres-js` / `postgres.js` is pinned. Even with `db.ts` written, the user has to bring their own driver. |
| Indexer for `events_accessibility_rewards` | not present | The whole pipeline reads pre-indexed events from a Postgres table populated by something **outside this repo**. There is no event-listening, RPC-fetching, or block-walking code anywhere in `settlement-handover/`. |

Even after `.env` and `db.ts` are populated, the pipeline still produces zero output until the upstream indexer has populated `events_accessibility_rewards` and `block_timestamp_accessibility_rewards` for every chain × contract listed in `calculations/config.ts`.

### 1.2 What the indexer needs to provide

Reading `calculations/helpers.ts::getAggregatedEventRows` and `getSwapAggregatedEventRows` against `db/schema.ts::EventsAccessibilityRewards`, the upstream indexer must:

- Source events from the contracts in `config.ts` (one entry per (network, contract)).
- Decode and store the following events into `events_accessibility_rewards`:
  - `Staked`, `Withdrawn` (Sky Farm / Spk Farm / Chronicle USDS staking contracts)
  - `Deposit`, `Withdraw`, `Transfer`, `Referral` (sUSDS, sUSDC, stUSDS, sp* ERC4626-style vaults)
  - `Swap`, `Transfer` (Base/Optimism/Arbitrum/Unichain PSM3 contracts where sUSDS is acquired via swap rather than `deposit()`)
- Persist `return_values` as JSON, including `referral` for `Referral` events (matched to its `Deposit` by `transaction_hash` at calculation time) and `referralCode` directly for `Swap` events.
- Populate `block_timestamp_accessibility_rewards` per (network, block_number) — this is the only source of wall-clock timestamps for time-weighted accrual.

The repo provides no scaffolding for any of this.

### 1.3 What it CAN compute, assuming a healthy upstream

If the DB and indexer are in place, the pipeline computes, per month, per (partner, network, farm):

- **TVL-weighted reward base** in original token units, accumulated as `Σ(tvl × Δt) / 31 536 000` across each user's deposit/withdraw/transfer events, attributed to the user's currently-active referral code.
- **Reward APY-equivalent amount** = base × `apyToAnnualizedDailyRate(rate)`, where `rate` is looked up per ref_code from `distribution_rewards_additional_percentage` (DB-driven; see §2.3 below).
- **Allocation-adjusted "reward to pay"** = reward × allocation, where allocation is `0.9` for sp* farms and `1.0` everywhere else (see §3.2 — this is a notable simplification).
- **USD-converted figures** for non-USDS tokens via Alchemy `tokens/historical` endpoint plus on-chain Deposit-event share/asset rates for stUSDS and sp*. USDS itself is hard-pegged to 1 USD (no conversion query).
- **Per-user TVL snapshots** persisted to `user_monthly_histories_accessibility_rewards`, used to resume calculation from the last completed month rather than re-running from genesis.

The output is monthly only. There is no daily layer and no 7-day rolling smoothing — those are dashboard concerns the repo does not address.

### 1.4 Configuration completeness summary

| Is it configured? | Item |
|---|---|
| ✅ | Farm × chain × contract address × ABI × monitored events list (`config.ts`) — comprehensive, ~16 farms |
| ✅ | Token decimals where non-18 (sp* and Base sUSDC) |
| ✅ | Reward-rate registry seeding logic (`syncAdditionalPercentagesFromPartners`) |
| ✅ | Hardcoded-known-partners short table (`{1001: 'summerfi', 1002: 'defisaver', 1004: 'paraswap', 1007: 'yearn'}`) |
| ❌ | DB connection (stubbed) |
| ❌ | `.env` values (blank) |
| ❌ | Postgres driver (not pinned) |
| ❌ | Event indexer (absent — repo is calculator only) |
| ❌ | `stars` table contents (referenced by `getStarIdForRewardCode` for ref_code → star ranges; no seed data) |
| ❌ | Initial `partners` table seeding (relies on auto-creation via `getOrCreatePartner`, with name '-' if not in the hardcoded short list) |

**Bottom line:** the repo is the *compute layer* of a larger system whose *data layer* lives elsewhere. Treating this repo in isolation, it can compute zero pieces of DR. Treating the system as a whole (assuming the upstream pieces exist on Amatsu's infrastructure), it computes a monthly per-partner reward number for the asset/chain matrix listed in §3.1.

---

## 2. Methodology — what it tells us, and how it lines up against `dr-query-analysis.md`

### 2.1 Attribution model: confirmed last-referral-wins, applied uniformly

The repo's deposit handler (`calculations/helpers.ts::processDepositEvent`) does this on every deposit that carries a referral code:

```ts
referralTVL[previousReferral] -= oldTVL
referralTVL[evt.referral]     += oldTVL
userState.referral = evt.referral
```

The user's **entire running balance** is moved from the previous referral bucket to the new one. Withdrawals (`processWithdrawalEvent`) reduce from `userState.referral` — i.e. whatever ref_code is currently active on that user — regardless of which ref_code attracted the deposits being withdrawn.

This is exactly the same attribution pattern as Dune's `query_5358161` (stUSDS) — but applied here to **every** asset and chain, including ones where Dune relies on opaque Spark pre-computed datasets (sUSDS, sUSDC, sp* vaults). And the repo searches turn up zero references to "FIFO" anywhere, confirming it is not a future plan either.

**Implications for `dr-query-analysis.md` Q1 (FIFO vs last-referral-wins):**

- This is independent corroboration that the production-deployed convention used by the Amatsu/handover team is last-referral-wins, not FIFO.
- The query analysis already noted that query_5358161 (the only self-owned reference Spark provides) implements last-referral-wins as a deliberate choice, not a contract limitation.
- It is now harder to argue FIFO is the intended interpretation in practice. Two independent teams have shipped last-referral-wins.
- The unresolved question becomes narrower: **does the spec need to be reconciled with the deployed convention, or does the deployed convention need to be reconciled with the spec?** Either way, our project should not assume FIFO is the answer just because the spec text says so.

**Note — snapshot cold-start and last-referral-wins interaction (Scenario B)**

The "entire running balance" re-tag (`oldTVL` in the code above) applies only to balances visible within the calculation's event window. Two code-level facts bear on this:

1. **`userHistories = {}` on first run (directly evidenced, line 391/427 of `calculations/helpers.ts`):** When no snapshot exists in the DB for a farm, `userHistories` is initialised to an empty map. All users start with TVL = 0.

2. **"Genesis" means the earliest block in the indexer's DB, not Ethereum block 1 (lines 329–348 of `queries.ts`):** `getGenesisBlockTimestamp` queries `block_timestamp_accessibility_rewards WHERE network = 'ethereum' ORDER BY block_number ASC LIMIT 1`. On first run for a new farm, the code correctly replays from this earliest block — but the events it processes come from `events_accessibility_rewards`, a table populated by the *external event indexer* (not in this repo). **If the indexer only began collecting stUSDS events in Oct 2025, the replay processes empty event lists for every month before Oct 2025, leaving `userHistories` at `{}` through Sep 2025.**

The assumption is therefore: the event indexer did not have historical stUSDS events before Oct 2025 — either because stUSDS was added to `config.ts` at that point, or because the indexer was not backfilling to the contract's genesis. We cannot confirm this without access to Amatsu's live `events_accessibility_rewards` table. Under this assumption:

- **Scenario A (simple gap):** A user deposited $100k into stUSDS in Aug 2025 with no referral code. If no events for that deposit exist in the indexer's DB, the replay sees a zero balance — the Aug 2025 deposit is invisible regardless of later code-197 activity.
- **Scenario B (retroactive re-tagging — most impactful):** The same user adds $1k in Oct 2025 using ref_code 197. Under last-referral-wins, a from-genesis replay re-tags the *entire* $101k (including the pre-existing $100k) to code 197 from that point. Amatsu, whose indexer only has the Oct 2025 deposit, attributes only the $1k to code 197.

**Empirical evidence supporting the assumption:** Our from-genesis replay of ref_code 197 (stusdsFarm) shows 2–3× higher totals than Amatsu's CSV for Oct 2025–Feb 2026. No rate difference exists in this period, so the divergence is structural. This is consistent with incomplete event history on Amatsu's side before Oct 2025. The ratio widens to 9–14× from Mar 2026 onward due to Amatsu's separate rate drop (see §2.3 / Q2).

**Open question:** Was the limited event history for stUSDS before Oct 2025 intentional (only deposits under active tracking are eligible) or an artefact of when the indexer was configured? Any new reconstruction system must decide whether to: (a) match Amatsu's paid figures by starting from the same cold-start month, or (b) replay from contract genesis — which will systematically overcount relative to Amatsu for any farm added mid-stream.

### 2.2 Compounding convention: matches Dune exactly

`apyToAnnualizedDailyRate(apy)` in `calculations/helpers.ts` is:

```ts
365 * (Math.exp(Math.log(1 + apy) / 365) - 1)
```

This is byte-for-byte the same formula as `query_5353955`'s `reward_per` derivation (continuously-compounded daily-rate equivalent of the target APY).

**Implications for Q9 (simple vs daily-compounded payout convention):**

- Both the Spark Dune side and the Amatsu repo use daily compounding.
- The spec text `DR = Address Net Deposits * 0.005 / 12` is the only place suggesting simple monthly. Two implementations agree against the spec text.
- This further supports treating daily-compounded as the de-facto authoritative convention.

### 2.3 Reward-rate registry: more sophisticated than Dune's range rule

The repo has a real ref_code registry, distributed across three tables:

- `partners(accessibility_reward_code, star_id, is_active, track_ssr_incentives)` — one row per ref_code, with operational flags.
- `stars(rewards_codes_range_from, rewards_codes_range_to)` — assigns ranges of ref_codes to "stars" (organisations / Prime Agents).
- `distribution_rewards_additional_percentage(ref_code, since_date, additional_percentage)` — time-versioned per-ref_code rate adjustments.

Seeding logic in `queries.ts::syncAdditionalPercentagesFromPartners` initialises:

| Period | Rule | Effective additional % | Total rate (= base 0.2% + additional) |
|---|---|---|---|
| Pre-2026 | `accessibility_reward_code == 'untagged'` | 0.2% | 0.4% |
| Pre-2026 | numeric `100 ≤ code < 1000` (Spark range) | 0.4% | 0.6% |
| Pre-2026 | else | 0.2% | 0.4% |
| 2026+ | all codes | 0.3% | 0.5% |

**Comparison to Dune `query_5353955`:**

| Dimension | Dune | This repo |
|---|---|---|
| Pre-2026 Spark rate | 0.6% | 0.6% (codes 100–999) |
| 2026+ default | XR 0.5%, XR* 0.2% | 0.5% (uniform) |
| Non-Spark default | 0.6% in `XR-stUSDS` / `XR` | **0.4%** in this repo |
| stUSDS rate divergence | Dune drops `XR-stUSDS` to 0.10% from Jan 2026 | Repo code treats stUSDS at 0.50% in 2026+ (no stUSDS-specific branch), but actual CSV output for code 197 shows a ~5.6× drop at Mar 2026, confirming a DB override applies ~0.10% from that month |
| Mechanism | Hardcoded `dune_user_generated.spark_dr_rates` table | DB-driven `distribution_rewards_additional_percentage` with effective dates and per-ref_code overrides |

**Implications for `dr-query-analysis.md`:**

- **Q10 (ref_code registry)** — confirmed: a registry exists in the Amatsu / handover stack. Better still, it supports per-code overrides with time versioning, which is what Q10's recommendation in §11.2 calls for. We should look at this schema as a possible blueprint for our Dune presentation layer.
- **Q2 (stUSDS rate drop to 0.10%)** — the repo code does not apply a stUSDS-specific drop (no farm-specific branch in `getRewardPercentage`). However, Amatsu's *actual CSV output* for stUSDS code 197 shows a ~5.6× drop between Feb and Mar 2026, consistent with a live DB override setting the effective rate to ~0.10% from that month. This means both Dune and Amatsu effectively use 0.10% for stUSDS, but Dune applies it from January and Amatsu from approximately March — a timing discrepancy, not a rate-level disagreement.
- **Pre-2026 non-Spark rate** — this repo's 0.4% does not match Dune's 0.6% XR rate. Two interpretations: (a) the repo's "non-Spark" path is for a class of integrators not currently tracked in Dune at all, or (b) one side has the wrong rate. This is worth a direct comparison against payout records if any exist. Either way it is a divergence and should be on the open-questions list.

### 2.4 Untagged ref_code reclassification

The repo reclassifies `'untagged'` to `'127'` only on `farm.l2 || partnerName === 'Sky Farm' || partnerName === 'Spk Farm'`. Dune reclassifies untagged (`-999999`) to `99` for sUSDS and `127` for sUSDC.

These do not match exactly. The repo's `127` reclassification is broader (covers all L2 sUSDS Swap-based flows, plus the Sky/Spk USDS staking contracts), while Dune splits 99 vs 127 by token. Likely cosmetic but worth flagging for any reconciliation work.

### 2.5 No FIFO infrastructure, period

Search confirms zero references to `FIFO`, `fifo`, "first-in", or any equivalent terminology in `settlement-handover/`. The repo's per-user state is `{tvl, referral, hasBeenTagged}` — a single scalar TVL plus the currently-active referral. There are no per-tranche structures, no deposit-history queues, no withdrawal-matching logic. FIFO is structurally impossible in the current data model and would require a meaningful refactor to introduce.

---

## 3. Scope and complexity vs. the Spark Dune queries

### 3.1 Asset, chain, and farm coverage

The repo's `FARM_CONFIGS` (in `schemas.ts`) plus `config.accessibilityRewards.partners` (in `calculations/config.ts`) define exactly what is tracked.

| Farm | Token | Chain(s) | Mechanism | Spark Dune coverage |
|---|---|---|---|---|
| Sky Farm | USDS | ethereum | Direct staking contract (`Staked`/`Withdrawn`/`Referral` events at `0x0650CAF1...`) — Etherscan label "Sky: Staking Reward"; Synthetix-style `StakingRewards` clone, USDS → SKY rewards | Partial: Dune's `result_spark_staked_usds_balances_by_referrals` appears to track only this contract's underlying USDS balance, opaquely (see §3.1 footnote) |
| Spk Farm | USDS | ethereum | Direct staking contract (`Staked`/`Withdrawn`/`Referral` events at `0x173e314C...`) — Etherscan label "Sky: USDS to SPK Farm (StakingRewards)"; same SNX clone, USDS → SPK rewards (`app.spark.fi/spk/farm`) | ❌ Not tracked in Dune (USDS-SPK symbol exists in main query as a remap branch but the underlying dataset never emits it) |
| Chronicle | USDS | ethereum | Direct staking contract (`Staked`/`Withdrawn`/`Referral` events at `0x10ab606b...`) — Chronicle Points farm, USDS → CLE points (10 pts = 1 CLE) | ❌ Not tracked in Dune at all |
| sUSDS Farm | sUSDS | ethereum | ERC4626 deposit/withdraw with `Referral` event | ✅ via Spark pre-computed dataset (opaque) |
| sUSDS Farm | sUSDS | base, arbitrum, optimism, unichain | PSM3 swap-based (`Swap` event, `referralCode` parameter inline) | ✅ Covered by Spark pre-computed dataset (opaque) — Arbitrum sUSDS confirmed live in query_5310067 sample (code 128 ≈ $3.5M); Base, Optimism, Unichain likely included. Note: a small number of Arbitrum rows have malformed large ref_codes from bytes32 parsing (negligible amounts). |
| sUSDC Farm | sUSDC | ethereum, base, arbitrum, optimism, unichain | ERC4626 deposit/withdraw with `Referral` event | ✅ Covered by Spark pre-computed dataset (opaque) — ethereum, base, and Arbitrum (code 128 ≈ $116M) confirmed live in query_5310067 sample; Optimism + Unichain likely included |
| stUSDS Farm | stUSDS | ethereum | ERC4626 + separate `Referral` event matched by tx_hash | ✅ via self-owned `query_5358161` (the reference implementation) |
| spUSDC Farm | spUSDC | ethereum, avalanche | Spark vault ERC4626 | ✅ via Spark pre-computed dataset (opaque) |
| spUSDT Farm | spUSDT | ethereum | Spark vault ERC4626 | ✅ via Spark pre-computed dataset (opaque) |
| spPYUSD Farm | spPYUSD | ethereum | Spark vault ERC4626 | ✅ via Spark pre-computed dataset (opaque) |
| — | spETH | — | — | ⚠️ Tracked-but-zero-rewards in Dune; **absent entirely from this repo** |
| — | USDS-SKY / USDS-SPK | — | — | Tracked by Dune via `result_spark_staked_usds_balances_by_referrals`; **absent entirely from this repo** |
| Spark Treasury (institutional AR) | USDS at single address | ethereum | Hardcoded address watch | Tracked by Dune via `query_5531933`; **absent entirely from this repo** |
| CoW Swap | sUSDS / others | multi-chain | Solver event tagging | Not in Dune; **not in this repo either** |

**What this confirms about the analysis:**

- **§5 Asset gap "general per-address USDS tracking is absent"** — partly addressed here. Sky Farm, Spk Farm, and Chronicle each track USDS staking-contract balances at the per-address level. Still does not cover arbitrary USDS holding (Token Rewards Contracts beyond these three), but it is a meaningful step beyond Dune. **Importantly, each of the three repo "farms" is a separate verified contract on Ethereum** — `0x0650CAF1...` (Sky Token Rewards / SKY emissions), `0x173e314C...` (Sky: USDS to SPK Farm), `0x10ab606b...` (Chronicle Points farm). All three are Synthetix-style `StakingRewards` clones with a referral-emitting wrapper. Dune's `result_spark_staked_usds_balances_by_referrals` appears to cover only the first one, opaquely — meaning Spk Farm and Chronicle are uncovered on the Dune side. The `USDS-SPK` remap branch in Dune's main query is a dead branch (no upstream rows ever emitted for it).
- **§7C sUSDS via PSM3 (corrected)** — Spark's Dune dataset now confirmed to cover L2 sUSDS including Arbitrum (Arbitrum sUSDS code 128 ≈ $3.5M confirmed in live query_5310067 output), so this is no longer a Dune gap for the chains confirmed. The Amatsu repo tracks all four chains (Base, Arbitrum, Optimism, Unichain) via the PSM3 `Swap` event and reads `referralCode` directly — the correct pattern for self-owned recreation. A self-owned implementation is still needed to replace the opaque Spark dataset and verify methodology.
- **USDS-SKY / USDS-SPK legacy symbols** — the repo does not carry them. This is consistent with the analysis's hypothesis (§3 Scenario B, §6) that those symbols may be legacy artifacts being phased out as users migrate to stUSDS.
- **Treasury (institutional AR) — out of scope** — the repo confirms operationally that Treasury is not part of integrator-facing DR. The query analysis (§6) inferred this; the Amatsu repo's exclusion of it is independent confirmation.
- **CoW Swap** — confirmed gap on both sides. Not implemented in either system.

### 3.2 Architectural model: fundamentally different

The Dune stack and this repo solve the same problem with very different architectures.

| Dimension | Spark Dune queries | settlement-handover repo |
|---|---|---|
| Substrate | SQL on Dune's pre-decoded event datasets | TypeScript event-replay on a Postgres-indexed event table |
| Per-user state during calculation | Collapsed away in `raw` CTE; aggregated to `(dt, blockchain, contract, token, ref_code)` | Preserved continuously: `userStates: Record<userKey, {tvl, referral, hasBeenTagged}>` |
| TWA computation | Daily segment products (`segment_balance_time_product / 86400`) | Continuous integration: `Σ(tvl × Δt) / 31 536 000` over event-driven intervals |
| Output cadence | Daily, with optional 7-day rolling smoothing | Monthly (no daily output) |
| FIFO feasibility from existing data | Not possible without rebuilding upstream sources | Not possible without changing the per-user state shape (single scalar TVL) |
| Deployment ratio for sp* | Per-day TWA from `result_spark_savings_v_2_vaults_time_weighted_average_holdings` | Hardcoded `SP_FARM_ALLOCATION = 0.9` for any farm whose name starts with `sp` and not `spk` |
| USD conversion | Per-token Dune queries (`query_5752873`, `query_5449435`, `query_5357785`) | Alchemy `tokens/historical` REST API + on-chain Deposit-rate inference |
| Resume / incremental computation | Re-runs full query each refresh (Dune materialised view caching) | Stateful: persists per-user TVL snapshot at month-end; resumes from latest snapshot |
| Multi-chain extension cost | New balance-source query + USD conversion query per chain | New row in `config.partners` array (with ABI + contract address) |
| Dependencies on opaque Spark datasets | Heavy: 4 Spark pre-computed datasets used directly | None — reads its own indexed events table |
| Auditability | Mixed: depends on whether you trust Spark's pre-computed datasets | Higher: every event used is in a queryable table the team controls |
| Code surface | ~9 SQL queries, ~hundreds of LoC of SQL | ~1,300 LoC of TypeScript across helpers + ~2,100 LoC of static config |

### 3.3 Where each is more sophisticated

**The Dune stack is more sophisticated at:**

- **Per-day deployment-ratio computation for sp* vaults.** The repo replaces this with a static 90% allocation. If the actual deployed fraction varies meaningfully (vault launches, large idle phases, market dislocations), the repo's static assumption is wrong, while Dune's per-day TWA stays correct. This is a real correctness gap in the repo.
- **Daily-granularity output and smoothing.** Useful for dashboarding and operational visibility. The repo computes only monthly.
- **Coverage of `result_spark_staked_usds_balances_by_referrals`** for the legacy USDS-SKY/USDS-SPK migration window.

**The repo is more sophisticated at:**

- **Per-user state preservation.** Every calculation has full per-user fidelity. Dune collapses to per-(day, contract, ref_code) before it touches the rate table.
- **Multi-chain extensibility.** Adding Optimism, Arbitrum, Unichain to the model required only config rows. Adding the same chains to Dune would require new balance-source queries and conversion-rate queries.
- **PSM3 swap-based tracking (Base + L2 sUSDS).** Implemented; Dune does not yet do this.
- **Direct USDS staking-contract tracking** (Sky Farm, Spk Farm, Chronicle). Goes beyond the single-address Treasury approach in Dune.
- **Time-versioned per-ref_code rate registry.** Supports operational adjustments (per-partner overrides, dated activations) that Dune's hardcoded rate table does not.
- **Auditability / no opaque dependencies.** Every event used is in a controllable Postgres table.
- **Resumable computation** with per-user snapshots — naturally suited to monthly payout cycles.

### 3.4 Where they happen to agree (worth keeping in mind)

- Compounding convention (`apyToAnnualizedDailyRate` formula) is identical.
- Last-referral-wins attribution (explicit in this repo, confirmed in `query_5358161` for stUSDS, suspected in the opaque Spark datasets for sUSDS / sp*).
- Untagged → 127 reclassification for sUSDC and L2 contexts (broadly aligned, with detail differences).
- No CoW Swap support on either side.
- No spETH rewards (Dune has zeros, repo simply omits it).

---

## 4. Net takeaways for our project

These are positions to carry into the next phase of the roadmap.

1. **The deployed reality is last-referral-wins.** Two independent implementations agree against the spec's "FIFO" wording. Our work should not assume FIFO is the correct target for V1 unless and until the spec owners explicitly resolve `dr-query-analysis.md` Q1 in favour of FIFO. Until then, building last-referral-wins is the lower-risk choice and matches what gets paid today.
2. **A ref_code registry concept is already proven on the Amatsu side.** The schema (`partners` + `stars` + `distribution_rewards_additional_percentage`) is a useful blueprint for our presentation-layer classification work (`dr-query-analysis.md` §11.2 / Q10). We can lift the shape of it without lifting the data.
3. **Several "gaps" in the Dune stack are already solved here.** Sky Farm / Spk Farm / Chronicle USDS tracking, Base sUSDS via PSM, Optimism/Arbitrum/Unichain sUSDS via PSM3, and per-ref_code time-versioned rate overrides are all functionally implemented. Where our project intends to fill those gaps from the Dune side, this repo is a useful reference for the contract addresses, event ABIs, and integration patterns — even if the SQL we write ends up looking nothing like the TypeScript here.
4. **Several Dune capabilities are missing from this repo.** Per-day sp* deployment ratio, daily-granularity output, USDS-SKY/USDS-SPK legacy coverage, spETH zero-tracking, and Spark Treasury institutional AR are all absent. Our Dune rebuild should retain these capabilities; we should not collapse to the repo's simpler model just because it is simpler.
5. **The repo confirms one open question and reframes another.**
   - **Q9 (compounding convention)** — both implementations agree on daily-compounded; we should treat that as authoritative.
   - **Q2 (stUSDS rate drop to 0.10%)** — the repo code doesn't apply this drop explicitly, but Amatsu's actual CSV output confirms ~0.10% was applied from approximately March 2026 via a live DB override. Both systems use 0.10%; the open question is the effective date (Dune: January, Amatsu: ~March).
6. **Cross-checking will require a shared payout reference.** If reconciliation against Amatsu's payouts is ever in scope, the agreement on compounding + attribution model means most reward numbers should be close. The two main causes of expected divergence are: (a) sp* deployment ratio (90% flat vs per-day TWA), and (b) the stUSDS rate drop in 2026+ if it is applied on one side and not the other.
7. **The repo is not a drop-in replacement for the Dune work.** It is a calculator that depends on an event indexer we do not have, a populated Postgres we do not have, and an Alchemy account we do not have. Even if all three were provided, it would not produce a Dune-style dashboard — it produces monthly payout records destined for a different downstream surface (Amatsu's own UI / DB). Our Dune dashboard remains the correct foundation for our deliverables; this repo is reference material, not a substitute.

---

## 5. Loose ends and open questions to revisit

- **Event indexer coverage for new farms.** When a farm (e.g. stUSDS tracking for ref_code 197) is first added to the pipeline, all per-user TVL snapshots start at zero (`userHistories = {}`, directly evidenced in `calculations/helpers.ts`). The code then replays from "genesis" — but "genesis" means the earliest block in the `block_timestamp_accessibility_rewards` table, and events come from `events_accessibility_rewards` (the external indexer). If the indexer only started collecting stUSDS events in Oct 2025, months before that are empty and all balances remain at zero through Sep 2025. This is an assumption (we cannot see the live DB), strongly supported by the 2–3× overcount in our stUSDS/code-197 reconstruction for Oct 2025–Feb 2026, where no rate difference exists. The question of whether to start from the same indexer start date as Amatsu, or replay from contract genesis, is a deliberate design choice that must be resolved before reconciliation is in scope. See §2.1 "Note — snapshot cold-start" for the full scenario breakdown.
- **Pre-2026 non-Spark rate divergence** (0.4% here vs 0.6% XR in Dune). Worth confirming directly against payout records if available.
- **`stars` table seed data.** The `getStarIdForRewardCode` mechanism uses numeric ranges, but the ranges themselves live in DB rows we do not have. If we ever stand up a similar registry, we will need that mapping.
- **Hardcoded `SP_FARM_ALLOCATION = 0.9`.** Where did 90% come from? Is this an older snapshot of the actual deployment ratio, or a policy floor? Worth understanding before we either inherit or contradict it in our work.
- **Why no spETH?** Dune intentionally tracks-with-zero-rewards. The repo simply omits it. Was that an oversight or a deliberate scoping decision in the Amatsu pipeline? If deliberate, on what reasoning?
- **Treasury / institutional AR.** Confirmed out of scope here. Confirms our hypothesis (§6 of the analysis) but does not tell us whether it should live in any DR-adjacent product at all, or in a fully separate institutional-tracking pipeline.

---

## 6. Token coverage matrix (reference)

A compact at-a-glance reference cross-cutting the asset side of §3.1. This is intentionally redundant with §3.1 — the cut here is **token × chain × address**, not by farm.

### 6.1 Tokens tracked by Amatsu (`settlement-handover/.../config.ts`)

These are the entries the calculator will accumulate per-user TVL on (one row per `partners[]` entry — 18 in total, covering 6 chains and 9 distinct token symbols).

| Token | Chain | Tracked contract (where events are read) | Underlying token address | Mechanism |
|---|---|---|---|---|
| USDS | ethereum | `0x0650CAF159C5A49f711e8169D4336ECB9b950275` (Sky Farm) | `0xdC035D45d973E3EC169d2276DDab16f1e407384F` | Synthetix-style `StakingRewards` clone, USDS → SKY emissions |
| USDS | ethereum | `0x173e314C7635B45322cd8Cb14f44b312e079F3af` (Spk Farm) | same USDS | Same SNX clone, USDS → SPK emissions |
| USDS | ethereum | `0x10ab606b067c9c461d8893c47c7512472e19e2ce` (Chronicle) | same USDS | Same SNX clone, USDS → CLE points |
| sUSDS | ethereum | `0xa3931d71877c0e7a3148cb7eb4463524fec27fbd` | self (ERC4626) | ERC4626 `Deposit`/`Withdraw` + `Referral` event |
| sUSDS | base | `0x1601843c5E9bC251A3272907010AFa41Fa18347E` (PSM3) | `0x5875eEE11Cf8398102FdAd704C9E96607675467a` | PSM3 `Swap` event with inline `referralCode` |
| sUSDS | optimism | `0xe0F9978b907853F354d79188A3dEfbD41978af62` (PSM3) | `0xb5B2dc7fd34C249F4be7fB1fCea07950784229e0` | Same PSM3 swap pattern |
| sUSDS | arbitrum | `0x2B05F8e1cACC6974fD79A673a341Fe1f58d27266` (PSM3) | `0xdDb46999F8891663a8F2828d25298f70416d7610` | Same PSM3 swap pattern |
| sUSDS | unichain | `0x7b42Ed932f26509465F7cE3FAF76FfCe1275312f` (PSM3) | `0xA06b10Db9F390990364A3984C04FaDf1c13691b5` | Same PSM3 swap pattern |
| sUSDC | ethereum | `0xBc65ad17c5C0a2A4D159fa5a503f4992c7B545FE` | self (ERC4626) | ERC4626 `Deposit`/`Withdraw` + `Referral` |
| sUSDC | base | `0x3128a0f7f0ea68e7b7c9b00afa7e41045828e858` | self | Same |
| sUSDC | arbitrum | `0x940098b108fb7d0a7e374f6eded7760787464609` | self | Same |
| sUSDC | optimism | `0xcf9326e24ebffbef22ce1050007a43a3c0b6db55` | self | Same |
| sUSDC | unichain | `0x14d9143becc348920b68d123687045db49a016c6` | self | Same |
| stUSDS | ethereum | `0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9` | self (ERC4626) | ERC4626 + separate `Referral` event matched by `tx_hash` |
| spUSDC | ethereum | `0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d` | self (ERC4626, 6 dec) | Spark vault ERC4626 |
| spUSDC | avalanche | `0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d` | self (6 dec) | Same |
| spUSDT | ethereum | `0xe2e7a17dFf93280dec073C995595155283e3C372` | self (6 dec) | Same |
| spPYUSD | ethereum | `0x80128DbB9f07b93DDE62A6daeadb69ED14a7D354` | self (6 dec) | Same |

**Pricing-only references** (Amatsu's `tokenPrices.tokenCodes` — fetched from Alchemy historical API, not balance-tracked):

| Token | Address | Used for |
|---|---|---|
| USDS | `0xdC035D45d973E3EC169d2276DDab16f1e407384F` | Hard-pegged to 1 USD; lookup defined but not actually queried |
| USDT | `0xdac17f958d2ee523a2206206994597c13d831ec7` | Underlying for spUSDT and stUSDS' deposit-rate inference path |
| PYUSD / pyUSD | `0x6c3ea9036406852006290770bedfcaba0e23a0e8` (duplicated under two casings) | Underlying for spPYUSD |
| USDC | `0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48` | Underlying for spUSDC and Base sUSDC |
| USDG | `0xe343167631d89B6Ffc58B88d6b7fB0228795491D` | Listed but no farm references it — anticipated future support |
| sUSDS, stUSDS, spUSDC, spUSDT, spPYUSD | (their farm contracts) | Self-pricing via on-chain Deposit-event share/asset rate |

### 6.2 Tokens our project plans to track (Atlas spec + Dune queries)

Combining `dr-atlas-spec.md` and the inventory in `dr-query-analysis.md`:

| Token | Chain | Tracked contract / dataset | Source | Status |
|---|---|---|---|---|
| sUSDS | ethereum | `0xa3931d71877c0e7a3148cb7eb4463524fec27fbd` | `result_spark_s_usds_s_usdc_time_weighted_average_balance` (opaque) → query_5310067 | Active; needs self-owned recreation |
| sUSDC | ethereum | `0xBc65ad17c5C0a2A4D159fa5a503f4992c7B545FE` | Same dataset | Active; needs self-owned recreation |
| sUSDC | base | `0x3128a0f7f0ea68e7b7c9b00afa7e41045828e858` | Same dataset | Active; needs self-owned recreation |
| sUSDS | arbitrum | `0xddb46999f8891663a8f2828d25298f70416d7610` | Same dataset (confirmed in live query_5310067 sample; code 128 ≈ $3.5M) | Active; needs self-owned recreation |
| sUSDC | arbitrum | `0x940098b108fb7d0a7e374f6eded7760787464609` | Same dataset (confirmed in live query_5310067 sample; code 128 ≈ $116M) | Active; needs self-owned recreation |
| sUSDS/sUSDC | optimism, unichain | (addresses in §6.1) | Same dataset (likely included; not yet confirmed from sample data) | Assumed active; verify from live query |
| stUSDS | ethereum | `0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9` | Self-owned `query_5358161` | Complete (subject to FIFO question) |
| spUSDC | ethereum | `0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d` | `result_spark_sp_usdc_sp_usdt_sp_eth_time_weighted_average_balance` (opaque) → query_5358295 | Active; needs self-owned recreation |
| spUSDT | ethereum | `0xe2e7a17dFf93280dec073C995595155283e3C372` | Same dataset | Active; needs self-owned recreation |
| spPYUSD | ethereum | `0x80128DbB9f07b93DDE62A6daeadb69ED14a7D354` | Same dataset | Active; needs self-owned recreation |
| spETH | ethereum | (Spark vault; address not enumerated in our notes) | Same dataset; `amount_deployed` hardcoded to 0 | Tracked-but-zero-rewards (intentional) |
| spUSDC | avalanche_c | `0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d` | Same dataset (sample shows live Avalanche rows under ref_code 128) | Active; needs self-owned recreation |
| USDS — Sky Farm staking | ethereum | Activity at staking contract `0x0650CAF159C5A49f711e8169D4336ECB9b950275`; Dune sees it only as aggregate USDS at `0xdC035D45...` | `result_spark_staked_usds_balances_by_referrals` (opaque, aggregate-only, labeled `USDS-SKY`) | Active but declining; gate at cutover (~2025-08-25). The `USDS-SPK` symbol in Dune's main query is a dead branch — it remaps but no rows are ever emitted. |
| USDS — Spark Treasury (institutional AR) | ethereum | `0x3300f198988e4C9C63F75dF86De36421f06af8c4` | query_5531933 | Active; **likely out of scope for DR** (§6 of analysis) |
| sUSDS via Base PSM | base | (Base PSM contract — not yet identified in our queries) | None — **gap** (§7C) | To build (Step 3 of §11.5) |
| USDS direct (general SSR / Token Rewards Contracts beyond Treasury) | ethereum | (per-address USDS holding tied to a DR ref_code) | None — **gap** (§5 / §7A) | To build (Step 5 of §11.5) |
| Any token via CoW Swap | multi-chain | CoW Swap solver events | None — **gap** (§7B / §8) | To build (Step 4 of §11.5) |

### 6.3 Overlap and gap summary

**Tracked at the same address on both sides** (low-friction reconciliation candidates):

| Token | Chain | Address |
|---|---|---|
| sUSDS | ethereum | `0xa3931d71877c0e7a3148cb7eb4463524fec27fbd` |
| sUSDC | ethereum | `0xBc65ad17c5C0a2A4D159fa5a503f4992c7B545FE` |
| sUSDC | base | `0x3128a0f7f0ea68e7b7c9b00afa7e41045828e858` |
| stUSDS | ethereum | `0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9` |
| spUSDC | ethereum | `0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d` |
| spUSDT | ethereum | `0xe2e7a17dFf93280dec073C995595155283e3C372` |
| spPYUSD | ethereum | `0x80128DbB9f07b93DDE62A6daeadb69ED14a7D354` |
| spUSDC | avalanche | `0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d` |

For these eight rows, a like-for-like comparison between the two systems is meaningful. Any disagreement on a reward number on these rows points to: attribution (Q1), rate table (Q2), or sp* deployment ratio (90% flat in Amatsu vs. per-day TWA in Dune).

**Same activity, different lenses** (one row — covered on both sides but not at the same level of fidelity, so direct number comparison is not apples-to-apples):

| Activity | Chain | Amatsu sees it as… | Dune sees it as… |
|---|---|---|---|
| USDS staked into Sky Farm (`StakingRewards` clone, USDS → SKY emissions) | ethereum | Events at staking contract `0x0650CAF159C5A49f711e8169D4336ECB9b950275`, **per-user**, attribution from `Referral` events | Aggregate USDS balance at the USDS token contract `0xdC035D45...`, **aggregate-only** (no `user_addr`), surfaced via opaque `result_spark_staked_usds_balances_by_referrals` and labeled `USDS-SKY` in the main query |

The `USDS-SPK` symbol that appears in Dune's main query alongside `USDS-SKY` is a dead branch — the underlying dataset never emits rows for it. So Spk Farm activity (next table) is genuinely uncovered on the Dune side, not just relabeled.

**Amatsu tracks; we do not** (gaps Amatsu fills that our plan currently does not — directly relevant to §5 and §7C of `dr-query-analysis.md`):

| Token | Chain | Address | Notes |
|---|---|---|---|
| USDS (Spk Farm) | ethereum | `0x173e314C7635B45322cd8Cb14f44b312e079F3af` | Untracked anywhere on the Dune side. |
| USDS (Chronicle) | ethereum | `0x10ab606b067c9c461d8893c47c7512472e19e2ce` | Untracked anywhere on the Dune side. |
| sUSDS (PSM3) | base | PSM at `0x1601843c...`, token at `0x5875eEE1...` | This is the §7C "Base sUSDS via PSM" gap — Amatsu has it. |
| sUSDS (PSM3) | optimism | PSM at `0xe0F9978b...`, token at `0xb5B2dc7f...` | Beyond §7C; extends PSM coverage to three more chains. |
| sUSDS (PSM3) | arbitrum | PSM at `0x2B05F8e1...`, token at `0xdDb46999...` | Same. |
| sUSDS (PSM3) | unichain | PSM at `0x7b42Ed93...`, token at `0xA06b10Db...` | Same. |
| sUSDC | arbitrum | `0x940098b108fb7d0a7e374f6eded7760787464609` | Untracked in Dune. |
| sUSDC | optimism | `0xcf9326e24ebffbef22ce1050007a43a3c0b6db55` | Untracked in Dune. |
| sUSDC | unichain | `0x14d9143becc348920b68d123687045db49a016c6` | Untracked in Dune. |

**We plan to track; Amatsu does not** (capabilities our Dune rebuild should retain):

| Token | Chain | Notes |
|---|---|---|
| spETH | ethereum | Tracked-but-zero-rewards on the Dune side; absent from Amatsu entirely. Open question — was the omission deliberate? |
| USDS — Spark Treasury (institutional AR) | ethereum | `0x3300f198988e4C9C63F75dF86De36421f06af8c4` via Dune's `query_5531933`. Amatsu does not include it, consistent with our hypothesis that Treasury is institutional, not DR. |
| Any token via CoW Swap | multi-chain | Gap on **both** sides — neither system covers this yet. |
| USDS direct beyond Sky/Spk/Chronicle farms | ethereum | Spec §A.2.2.8.1.2.1.2.2.1 allows tracking USDS in arbitrary Token Rewards Contracts. Amatsu covers three known farms; full general support is a gap on both sides. |

**Token symbols Amatsu prices but does not balance-track** (informational; not a coverage gap):

USDT, PYUSD/pyUSD, USDC, USDG. These exist only as price-lookup entries for stUSDS' deposit-rate inference and the underlying assets of sp* vaults. They never appear as `partners[]` farms.

**One-sentence bottom line.** The eight rows in the first sub-table above are the only places where a direct numerical comparison makes sense; everything else is one-sided coverage that should inform what we keep, what we add, and what we de-scope as the roadmap progresses.
