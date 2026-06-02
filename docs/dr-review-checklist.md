# Distribution Rewards Tracking — Pre-Build Review Checklist

This document summarises the scope, methodology, open questions, and contract inventory for the Distribution Rewards (DR) Dune query rebuild.

---

## 1. Token and Contract Coverage

All contracts listed are on Ethereum mainnet unless noted. All three USDS staking contracts share the same Synthetix-style `StakingRewards` ABI (`Staked` / `Withdrawn` / `Referral` events).

### USDS Staking (Token Rewards Contracts)

| Farm | Contract | Notes |
|---|---|---|
| Sky Farm | `0x0650CAF159C5A49f711e8169D4336ECB9b950275` | USDS → SKY rewards. Rate: XR |
| Spk Farm | `0x173e314C7635B45322cd8Cb14f44b312e079F3af` | USDS → SPK rewards. Rate: XR |
| Chronicle | `0x10ab606b067c9c461d8893c47c7512472e19e2ce` | USDS → CLE points. Rate: XR |

> Currently only Sky Farm is partially covered in Dune (opaque, aggregate-only). Spk Farm and Chronicle have **zero** Dune coverage.

---

### sUSDS (ERC4626 Deposit/Withdraw + Referral event)

| Chain | Tracked contract | Mechanism |
|---|---|---|
| Ethereum | `0xa3931d71877c0e7a3148cb7eb4463524fec27fbd` | `Deposit`/`Withdraw` with inline `referralCode` parameter |
| Base | PSM3 `0x1601843c5E9bC251A3272907010AFa41Fa18347E` | `Swap` event with inline `referralCode` |
| Arbitrum | PSM3 `0x2B05F8e1cACC6974fD79A673a341Fe1f58d27266` | Same PSM3 swap pattern |
| Optimism | PSM3 `0xe0F9978b907853F354d79188A3dEfbD41978af62` | Same PSM3 swap pattern |
| Unichain | PSM3 `0x7b42Ed932f26509465F7cE3FAF76FfCe1275312f` | Same PSM3 swap pattern |

> sUSDS token addresses for L2s: Base `0x5875eEE1...`, Arbitrum `0xdDb46999...`, Optimism `0xb5B2dc7f...`, Unichain `0xA06b10Db...`
> L2 sUSDS has **zero** Dune coverage today. The Amatsu pipeline already tracks all four chains.

Rate: XR

---

### sUSDC (ERC4626 Deposit/Withdraw + Referral event — same template as sUSDS)

| Chain | Contract |
|---|---|
| Ethereum | `0xBc65ad17c5C0a2A4D159fa5a503f4992c7B545FE` |
| Base | `0x3128a0f7f0ea68e7b7c9b00afa7e41045828e858` |
| Arbitrum | `0x940098b108fb7d0a7e374f6eded7760787464609` |
| Optimism | `0xcf9326e24ebffbef22ce1050007a43a3c0b6db55` |
| Unichain | `0x14d9143becc348920b68d123687045db49a016c6` |

> Ethereum + Base partially covered in Dune via opaque Spark dataset. Arbitrum, Optimism, Unichain have **zero** Dune coverage.

Rate: XR*

---

### stUSDS

| Chain | Contract | Notes |
|---|---|---|
| Ethereum | `0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9` | ERC4626 + **separate** `Referral` event matched on `tx_hash` |

> Fully covered in Dune via self-owned `query_5358161` (reference implementation).
> Note: rate controversy — see §4 and open question Q2.

Rate: XR-stUSDS (disputed — see §4)

---

### Spark sp* Vault Tokens (ERC4626)

| Token | Chain | Contract | Asset decimals |
|---|---|---|---|
| spUSDC | Ethereum | `0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d` | 6 |
| spUSDC | Avalanche C | `0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d` | 6 |
| spUSDT | Ethereum | `0xe2e7a17dFf93280dec073C995595155283e3C372` | 6 |
| spPYUSD | Ethereum | `0x80128DbB9f07b93DDE62A6daeadb69ED14a7D354` | 6 |
| spETH | Ethereum | — | **Zero DR rewards — intentionally excluded** |

> DR reward base for sp* tokens is the **deployed fraction** of vault assets (deployment ratio), not raw TVL. Currently hardcoded at 90% flat in the Amatsu pipeline; the Dune query sources a per-day ratio from an opaque Spark dataset. See §3 (methodology) and open question Q8.

Rate: XR*

---

### Legacy Dataset Labels (to be replaced)

`USDS-SKY` and `USDS-SPK` are **not separate tokens** — they are synthetic labels used by the Spark Dune dataset `result_spark_staked_usds_balances_by_referrals` to represent plain USDS (token: `0xdC035D45d973E3EC169d2276DDab16f1e407384F`) deposited into the Sky Farm and Spk Farm staking contracts respectively. The underlying farms and contracts are listed above in §1 and remain active.

This dataset is being replaced by self-owned direct queries against the Sky Farm / Spk Farm / Chronicle contract events (see §2). Once replaced, these synthetic labels disappear entirely — the output would simply show USDS with the appropriate staking contract as the source.

- **USDS-SKY:** Dataset shows ~97% balance decline Sep 2024 → Dec 2025 as users migrated to stUSDS. Not relevant to the rebuilt query since we read directly from Sky Farm contract events.
- **USDS-SPK:** Appears to be a dead branch — no rows observed in the dataset. See Q3.

---

## 2. Tracking Methodologies Beyond Standard On-Chain

The spec defines four tracking methodologies ([Atlas A.2.2.8.1](https://sky-atlas.io/#e632c38f-3e4e-4c7e-acfd-b6ec45a422e6)). The standard on-chain method (ETH Mainnet General Tracking) covers the farms in §1 and is the primary focus of the build. The remaining three methodologies are listed below — they either require separate implementation work or need scoping confirmation.

### A. CoW Swap Tracking ([Atlas A.2.2.8.1.2.1.2.2.2](https://sky-atlas.io/#1b5cc0ee-0ee8-467e-ab49-33c06ad417dc))
- The spec describes this as using the same net deposit logic as general tracking, but tracked on CoW Swap's solver network events rather than direct contract deposits.
- When a user acquires sUSDS via a CoW Swap trade, there is no Deposit event on the sUSDS contract — the ref_code attribution must come from the solver event instead.
- In practice this would use last-referral-wins attribution, same as all other tracking (see Q1 and §3).
- **Status:** Not implemented in Dune or in the Amatsu pipeline.
- **Proposed approach:** Build as an isolated query producing per-address ref_code attribution claims for CoW Swap acquisitions. Where CoW Swap and the on-chain tracker assert conflicting ref_codes for the same (address, date), emit a conflict row — precedence resolution is a policy decision, not a query decision.
- See Q7.

### B. Base / L2 PSM3 Tracking ([Atlas A.2.2.8.1.2.1.2.2.3](https://sky-atlas.io/#f710bddf-dc1d-483c-9503-483574cb6333))
- The spec describes this as using the same net deposit logic as general tracking, but tracking reward codes as a parameter in calls to the PSM3 contract on Base and equivalent L2s.
- Conversions from USDS or USDC to sUSDS via PSM3 are treated as deposits.
- The ref_code is embedded inline in the PSM3 `Swap` event — no separate referral event matching needed.
- In practice this would use last-referral-wins attribution, same as all other tracking (see Q1 and §3).
- **Status:** Not covered in Dune. Already tracked in the Amatsu pipeline across Base, Arbitrum, Optimism, and Unichain (contract addresses in §1).

### C. Alternative Tracking Methods ([Atlas A.2.2.8.1.2.1.2.2.4](https://sky-atlas.io/#5eba1c21-4e93-4a0a-aa10-e99bcfa65f16))
- Prime Agents and GovOps can propose custom tracking methods, provided they:
    - Estimate USDS balances attributed to a reward code on a net deposit basis.
    - Prevent any possibility of the same USDS balance being double-counted across reward codes.
    - Use either on-chain data or off-chain data that can be independently verified.
- The Spark sp* vault tokens (spUSDC, spUSDT, spPYUSD) with the XR* rate are an instance of this — they use a Spark-specific deployment ratio rather than the standard SSR deposit parameters.
- **Note:** Any new alternative tracking methods proposed by integrators in the future would need to be evaluated and incorporated. No new instances are currently known beyond the existing sp* vaults.

---

## 3. Calculation Methodology

### Event Tracking
- For staking farms (Sky/Spk/Chronicle): `Staked`, `Withdrawn`, `Referral` events.
- For ERC4626 farms (sUSDS, sUSDC, stUSDS, sp*): `Deposit`, `Withdraw`, `Referral` events (and optionally `Transfer` for peer-to-peer share movement).
- For PSM3 L2 sUSDS: `Swap` event with `referralCode` inline.
- Referral code is read from: the deposit/staked event directly (sUSDS, sUSDC, staking farms) OR a separate `Referral` event in the same transaction matched by `tx_hash` (stUSDS, sp* vaults).

### Attribution Model: Last-Referral-Wins (not FIFO)
- At any point in time, a user's **entire running balance** is attributed to their most recently used referral code.
- When a user deposits with a new ref_code, the full balance (including prior deposits) shifts to the new code immediately.
- The spec text says "FIFO basis" but **both** the Amatsu pipeline and Dune's self-owned stUSDS query (`query_5358161`) implement last-referral-wins as a deliberate choice. ⚠️ See open question Q1 — this should be confirmed before build.
- `untagged` ref_code: Amatsu reclassifies to `127` for L2/sUSDC flows and USDS staking. Dune reclassifies `-999999` to `99` (sUSDS) or `127` (sUSDC). Alignment needed.

### Time-Weighted Average (TWA)
- Per-user balance × time-delta, accumulated across all events, divided by total window seconds.
- `TWA = Σ(balance × Δt) / window_seconds`
- Daily granularity for dashboarding; monthly rollup for payout.

### Reward Formula
- **Daily accrual:** `tw_reward = TWA_balance / 365 × reward_per`
  where `reward_per = 365 × ((1 + APY)^(1/365) - 1)` — annualised daily-equivalent of the APY using daily compounding.
- **Spec formula (simple interest):** `DR = net_deposits × APY / 12` per month.
- These two differ by ~1.5% per month. ⚠️ See open question Q9 — confirm authoritative payout convention.

### sp* Deployment Ratio
- Reward base for spUSDC, spUSDT, spPYUSD = `vault_total_assets × deployment_ratio`, where `deployment_ratio = (total_assets - idle_holdings) / total_assets`.
- Amatsu uses a flat **90%** approximation. Dune sources a per-day TWA deployment ratio from an opaque Spark dataset. ⚠️ See open question Q8.

### Proposed Dune Query Architecture (Layer Model)
```
Layer 0: Raw Events         — one query per (chain, contract); events only, no attribution
Layer 1: Per-user Attribution — ref_code assignment using last-referral-wins per (user, contract)
Layer 2: TWA Aggregation    — UNION ALL of Layer 1; sum across users per (dt, chain, token, ref_code)
Layer 3: Daily Reward Accrual — Layer 2 × reward rate table; exposes reward_base explicitly
Layer 3.5: Monthly Payout   — monthly rollup per DR spec formula (formula pending Q9)
Layer 4: USD Presentation   — apply per-token conversion rates; sUSDC shares sUSDS rate (documented approximation)
Presentation overlay        — referral_type tagging (Spark-internal vs external), driven by ref_code registry (Q10)
```

### Key Design Principles
- FIFO/attribution logic must live at **Layer 1** (per-user) — cannot be done at Layer 2 or later once user_addr is aggregated away.
- Rate lookup (`query_5353955`) is a shared component — unchanged.
- Each chain+contract is a separate Layer 0/1 entry; adding a new chain requires no changes above Layer 2.

---

## 4. Rate Exceptions and Historical Periods

| Rate code | Applies to | 2024–2025 | 2026+ | Notes |
|---|---|---|---|---|
| XR | sUSDS, USDS staking (Sky/Spk/Chronicle) | 0.60% | **0.50%** | |
| XR-stUSDS | stUSDS | 0.60% | **0.10%** | ⚠️ Amatsu pipeline uses **0.50%** in 2026+ — direct conflict with Dune. Must resolve (Q2) |
| XR* | sUSDC, spUSDC, spUSDT, spPYUSD | 0.60% | **0.20%** | Lower tier for alternative-method assets |
| AR | Spark Treasury USDS (institutional) | SSR + 0.60% | SSR + 0.20% | Not a DR code per spec; likely out of scope (Q6) |
| AR* | — | SSR + 0.20% | **Dropped** | Defined in rate table but never used in main query; treat as deprecated |
| spETH | spETH | 0% | 0% | Intentionally zero; tracked for display only |

#### Historical / Per-Referral-Code Rate Overrides (Amatsu pipeline)
The Amatsu pipeline supports **per-ref_code additional percentage overrides** with date ranges (stored in `distribution_rewards_additional_percentage` DB table). Known instances:
- Spark.lend / ref_code 128: additional rate applied above a base, with specific effective date ranges.
- These overrides are time-versioned — the rate can change at a specific date for a specific code, independently of the global rate table.
- ⚠️ If equivalent per-code overrides exist outside the global Dune rate table (`query_5353955`), they need to be enumerated and incorporated. Confirm whether any per-code rate exceptions exist beyond what is already in `query_5353955`.

---

## 5. *(Section reserved)*

---

## 6. Open Questions — Requesting Clarification

The following must be resolved before or during the build. Items are roughly ordered by how much they block.

---

**Q1 — Attribution model: last-referral-wins or FIFO?** *(Highest priority — blocks all Layer 1 build work)*

The spec states "FIFO basis." Both the Amatsu pipeline and Dune's self-owned stUSDS query implement last-referral-wins instead. Under last-referral-wins, a user's full balance shifts to the new ref_code on re-deposit; under FIFO, only the marginal new deposit is attributed to the new code and old deposits remain attributed to their original code until withdrawn. FIFO prevents gaming (re-tagging a large existing balance to a new integrator), but last-referral-wins is simpler and may reflect deliberate product intent.

- **Request:** Confirm which model is the intended, authoritative standard for DR attribution. If last-referral-wins is intended, acknowledge the divergence from the spec text so it can be documented.

---

**Q2 — stUSDS rate in 2026+: 0.10% (Dune) or 0.50% (Amatsu)?**

Dune's rate table (`query_5353955`) drops `XR-stUSDS` from 0.60% to 0.10% after 2026-01-01, while sUSDS stays at 0.50%. The Amatsu pipeline applies the same 0.50% to stUSDS in 2026+ with no stUSDS-specific drop. This is a concrete, confirmed disagreement between two production systems.

- **Request:** What is the correct 2026+ rate for stUSDS?

---

**Q3 — Did the Spk Farm (USDS-SPK) ever go live?**

The main Dune query contains remap logic for a `USDS-SPK` symbol, implying Spk Farm was expected to emit staked USDS balances — but no rows with that symbol have ever been observed in the upstream dataset. It is unclear whether Spk Farm staking was never launched, was tracked under a different name, or simply never attracted deposits.

This matters for scoping: if Spk Farm has never had any staked balances, tracking it may be lower priority.

- **Request:** Has Spk Farm (`0x173e314C7635B45322cd8Cb14f44b312e079F3af`) ever had user deposits? If so, why does the USDS-SPK symbol not appear in the Dune dataset?

---

**Q4 — Can `result_spark_staked_usds_balances_by_referrals` be retired in favour of the direct Sky Farm contract tracker?**

The plan is to replace this opaque, aggregate-only Spark dataset with a self-owned per-user query reading directly from the Sky Farm contract events (§2A).

- **Request:** Is there any data in `result_spark_staked_usds_balances_by_referrals` that the direct Sky Farm event tracker would not capture, that needs to be preserved?

---

**Q5 — Are there per-referral-code rate overrides that are not in Dune's rate table?**

The Amatsu pipeline's reward rate logic works as follows: there is a global `BASE_RATE` (0.20%) plus a per-ref_code `additionalPercentage` fetched from a live database table (`distribution_rewards_additional_percentage`) for a given date. The total rate for a code is `BASE_RATE + additionalPercentage`. The database table is not in-repo and its contents are not visible to us.

Dune's `query_5353955` defines a global rate table (XR, XR*, XR-stUSDS, etc.) but has no per-ref_code override mechanism. If some codes have custom rates in Amatsu's database that differ from the global Dune rates, those would silently be applied on the Amatsu side but missed on the Dune side.

As a concrete example: ref_code 128 (Spark.lend) — which we have been reconciling — could have an `additionalPercentage` in that database table that differs from the standard XR rate Dune applies to it. We cannot determine this from the repo code alone because `getAdditionalPercentagesForDate(db, date)` is a live database query with no hardcoded fallback values.

- **Request:** Provide a full list of ref_codes that have entries in `distribution_rewards_additional_percentage`, with their rate and effective date range. Are any of these codes currently assigned rates in Dune's `query_5353955` that differ from what the Amatsu database table specifies?

---

**Q6 — Should the Spark Treasury tracking (`query_5531933`) be in scope for DR?**

This query tracks a single hardcoded address (`0x3300f198988e4C9C63F75dF86De36421f06af8c4` — Spark's own treasury) earning the AR rate (SSR + spread). This is Spark-internal institutional reward tracking, not Distribution Rewards to integrators. The Amatsu pipeline does not include it.

- **Request:** Confirm whether Spark Treasury tracking is in or out of scope for the DR rebuild. If in scope, what is the intended treatment?

---

**Q7 — CoW Swap tracking: confirm it is in scope and needs to be built from scratch**

CoW Swap is listed in the spec as a supported tracking method. It is not currently implemented in Dune or in the Amatsu pipeline. The mechanism for sourcing referral codes from CoW Swap solver events is known and the tracker would need to be designed and built from scratch.

- **Request:** Confirm CoW Swap tracking is in scope for this build. If yes, are there any constraints or existing data sources that should be incorporated?

---

**Q8 — sp* deployment ratio: what is the correct per-day calculation, and can the Spark dataset dependency be replaced?**

The DR reward base for spUSDC/spUSDT/spPYUSD is the deployed fraction of vault assets. The Amatsu pipeline uses a flat 90% approximation. Dune sources a per-day deployment ratio from `result_spark_savings_v_2_vaults_time_weighted_average_holdings` — an opaque Spark dataset whose source events are unknown. Replacing this dependency is required to make the sp* tracking fully self-owned and auditable.

- **Request:** What events or on-chain data should be used to compute the per-day deployment ratio independently of the Spark dataset? What is the authoritative definition of "deployed" vs. "idle" assets in the sp* vaults?

---

**Q9 — Authoritative payout convention: simple monthly or daily compound?**

The spec states `DR = net_deposits × 0.005 / 12` (simple monthly). Both Dune and the Amatsu pipeline use daily-compounded accrual: `reward_per = 365 × ((1+APY)^(1/365) - 1)`. These differ by ~1.5% per month. This matters for reconciliation against payout records and for the design of the monthly rollup layer.

- **Request:** Which convention is authoritative for the official monthly DR payout calculation — the spec's simple formula or the daily-compounded formula used in both current implementations?

---

**Q10 — Where can an authoritative list of all active referral codes be obtained?**

For the Dune query to correctly label and filter DR output, it needs to know which ref_codes are active and what they map to. The current classification in Dune (codes 100–999 = "Spark referrals") is a brittle range heuristic. What is needed is a canonical source of truth for which codes exist and are valid DR codes.

- **Request:** Is there an authoritative list or registry of all referral codes (e.g. a database table, a spreadsheet, or an admin UI) that can serve as the source for this? If not, should one be created as part of this project?

---

**Q11 — Confirm Chronicle farm scope and rate**

Chronicle (`0x10ab606b067c9c461d8893c47c7512472e19e2ce`) is a USDS staking farm run by Chronicle Labs that rewards stakers with CLE points. It uses the same Synthetix-style `StakingRewards` contract design as Sky Farm and Spk Farm (`Staked`/`Withdrawn`/`Referral` events), so it requires no special tracking logic — the same parameterized template covers all three. It is tracked in the Amatsu pipeline under the same XR rate as the other USDS staking farms.

- **Request:** Confirm Chronicle is in DR scope and that the XR rate applies. If it earns a different rate or has other exceptions, please specify.
