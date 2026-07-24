# CowSwap 1003: double-attribution audit + HyperSync port design

**Date:** 2026-07-23. **Scope:** Ethereum (99.4% of CowSwap→sUSDS volume).
**Data:** raw on-chain logs via HyperSync (sUSDS `Deposit`/`Referral`/`Transfer`,
GPv2 `Trade`), balances via `eth_call` at the settlement cutoff block
**25433933** (2026-06-30 23:59 UTC). Window scanned: block 20651993 (2024-09-01)
→ 25595153. Analysis scripts: session scratchpad (`cow_fetch.py`,
`cow_analyze.py`, `cow_broad.py`, `cow_balances.py`).

## Verdict

The concern is **confirmed**. The 1003 methodology in
`diagnostic-queries/diag_susds_cowswap_1003_monthly_dr.sql` is a *standalone*
query that sticky-tags a wallet and attributes its **entire forward balance**
to 1003, terminated **only** by a later real `Referral`/PSM3 code. Because it
is computed outside the main TWA stream, every dollar it attributes is *also*
attributed by `twa_susds_susdc_erc4626.sql` — to untagged→99 (a **paid**
bucket) or to a real partner code — and synthetic tags from different
aggregator programs never terminate each other. This violates the Atlas
requirement (A.2.2.8.1.2.1.2.2.4): *"no possibility that the same USDS balance
is double counted for multiple reward codes."*

## On-chain evidence

### 1. The strict signal (the diag query's CTE) tags ONLY intermediary contracts

All sUSDS `Deposit` events inside GPv2 settlement txs: 5,601 events,
**46 distinct `Deposit.owner`s — 46/46 are smart contracts, zero EOAs**
(solvers, routers, vaults). Among them:

- `0x9008…ab41` — the GPv2 settlement contract itself
- `0x0083…4d10` — Curve PSM, which the main TWA **deliberately excludes** as a
  double-count guard (`TEMPLATE_A_EXCLUDED`); the diag query would pay DR on it
- `0xdef1…ee57` — Paraswap Augustus v5 (would be "cow"-tagged!)
- `0xba13…9ba9` — Balancer V3 vault (pooled funds)

These 46 contracts held ~2.4M sUSDS at audit time, and $2.9B of *later
non-CowSwap* deposits flowed through them post-tag — all captured by sticky
1003. The strict signal mis-targets 100% of its attribution: end users never
receive minted shares directly; solvers deposit and the settlement then
transfers sUSDS to the order owner.

### 2. The broad signal (Amatsu-scale): three quantified double-count cohorts

Broad 1003 user set = recipient of an sUSDS transfer **from the settlement**
inside a settlement tx (this is how end users actually receive
CowSwap-bought sUSDS): **1,206 wallets** (1,053 EOAs, 153 contracts). At the
2026-06-30 cutoff (all figures lower bounds — ~12% of balance calls were
rate-limited):

| Cohort | Wallets | sUSDS @ cutoff | Double count vs |
|---|---|---|---|
| Never referred (main TWA says 99) | 976 | **≥29.2M** | paid untagged bucket 99 |
| Prior real partner code, none after | 102 | **≥6.8M** | partner codes (mostly 128 Spark.lend, 1) |
| Tagged by BOTH CowSwap and Paraswap flows, no terminating referral | 48 | **≥3.5M** | the other aggregator's synthetic code |
| Broad-tagged contracts (pools) | 153 | **≥12.6M** | pooled funds counted under holders/venues (incl. Uniswap v4 PoolManager `0x0000…8a90`, 720k sUSDS) |

At the 2026 non-Spark XR rate (0.5%/yr), the 99- and partner-code overlaps
alone (~36M sUSDS) would double-pay ≈ **$180k/yr** if the standalone output
were added to the combine step naively.

Concrete examples (verifiable on Etherscan):

- **The exact reported scenario** — `0xd3e0d660d8fab05b34ccb7fe7681628d9a46c675`:
  bought 1.72M sUSDS via CowSwap (block 22168904, tx `0xa590265e40d9a5b5d694
  b32f6f1126eeddd880443435d6a164f2b6affae2533f`), later bought 2.79M sUSDS via
  Paraswap (block 23698601, tx `0x4882122542e4d6019d2e1bbd8022bf0c05e775460db7
  ebb47d7a14de16bf68ed`). The Paraswap tx emits **no user-level Referral**, so
  the 1003 tag never ends; a 1004 tracker built the same way tags the wallet
  too → both programs claim the same 700k sUSDS balance indefinitely.
- **Partner-code overlap** — `0x41bc7d0687e6cea57fa26da78379dfdc5627c56d`:
  3.86M sUSDS at cutoff, referred under code 128 (Spark.lend), then bought via
  CowSwap (tx `0x6f8bc358c956acae3fe76ab266470b69da2622b58d5d00998249c17458b0
  1f15`) with no later referral → main TWA pays 128, standalone 1003 pays cow,
  on the same balance.
- **Sticky capture** — 87 tagged EOAs later made direct (untagged) deposits
  totalling **$1.78B**; under sticky logic all of it accrues to 1003.

### 3. Why the whole aggregator class has this problem

All 926 sUSDS `Referral(1004)` events land on **Paraswap router contracts**
(`0x6a00…1068` ×448, `0x0000…380a` ×292, `0x00c6…06e0` ×182, `0xdef1…ee57` ×4)
— never on end users. Same pattern for 4011 (426 events). The routers' net
balance is ~0 (they forward within the tx), so in the main TWA those partner
codes earn ~nothing and their users sit in 99. This is what the removed
`referral_per_tx_fallback` CTE tried to fix by cross-event re-routing — the
right instinct, wrong mechanism (no user constraint). Any per-program
standalone re-derivation reintroduces double counting.

## Fix: one attribution stream, synthetic tags as pseudo-referral legs

Attribution must be computed **once per (wallet, token)** with all signals —
real `Referral` events, PSM3 swap codes, and synthetic aggregator tags —
competing in the same last-wins forward-fill. Double counting is then
impossible *by construction*: a tag re-labels balance segments instead of
adding a second copy, and any later signal (real or synthetic) ends the
previous tag — exactly the required "the DR for cowswap should end" semantics.
Total DR across codes is invariant (pure re-labeling of the same TWA).

Implementation in the HyperSync pipeline (`py/drhs/sources/template_ab.py`):

1. **New pseudo-referral source.** For each settlement tx, compute each
   wallet's **net sUSDS delta across the tx** from the already-fetched
   `Transfer` rows. Wallets with net delta > dust whose inflow came (directly
   or via the settlement) out of GPv2 get a pseudo-referral
   `(tx, wallet) → 1003` at that leg's `(block, log_index)`.
   - The net-delta rule filters solvers/routers/the settlement itself (they
     forward within the tx, net ≈ 0) with no extra data.
   - Cheap fetch: one topic-filtered query (`Transfer` with
     `topic1 = settlement`, 18,314 rows total) — no 3.2M-row `Trade` scan
     needed; sUSDS only leaves the settlement during settlements. Optionally
     verify with a light `Trade`-topic1 fetch per tx set.
2. **Merge, don't add.** Feed these into the existing
   `latest_referral_from_events` map with **real Referral events taking
   precedence** for the same `(tx, wallet)`. The TWA engine
   (`py/drhs/twa.py`) already forward-fills last-wins — no engine change.
3. **Keep the guards.** `TEMPLATE_A_EXCLUDED` still applies; extend it if a
   pooled venue shows up net-positive (audit list above: Uniswap v4
   PoolManager, Balancer V3 vault, Pendle SY, …). Rule of thumb: any tagged
   *contract* must be individually reviewed before its code is considered
   payable — this audit is the checklist.
4. **No standalone synthetic queries in combine.** 1003 flows out of the
   existing susds monthly rollup as just another `ref_code`. Retire
   `diag_susds_cowswap_1003_monthly_dr.sql` as a diagnostic only.
5. **Same mechanism for the other router programs** (1004 Paraswap, 4011)
   if/when they must be paid: pseudo-referral legs keyed on the router's
   delivery, in the same stream. Their tags and 1003 then terminate each other
   naturally — the 48-wallet overlap cohort becomes a sequence of correctly
   split segments.
6. **Out of scope / later:** L2 (PSM3 swap-in-settlement-tx, ~0.6% of volume),
   stUSDS and farm-balance 1003 (Amatsu attributes 1003 across sUSDS, stUSDS
   *and* legacy farms — cross-venue tag propagation is a separate methodology
   decision), FIFO vs last-referral-wins (global convention question, not
   1003-specific).

## Implemented + measured (2026-07-23)

The fix is implemented in `py/drhs/sources/template_ab.py`
(`SyntheticProgram` / `synthetic_referrals` / `merge_referrals`), wired as
`SPECS["susds_eth"].synthetic = (COWSWAP,)`. Dune-parity fixtures and
`validate*.py` intentionally compare the pre-synthetic path
(`include_synthetic=False`) since the Dune queries carry no 1003.

Measured on the full sUSDS-eth history (668,622 transfers, 13,469 referrals;
10,753 pseudo-referral tags on 1,144 wallets; settled window < 2026-07-01),
by diffing the source with/without the program (only affected wallets can
change, so the diff is exact):

**1003 DR (USD/month), Sep 2024 – Jun 2026: $885,145 total.** Recent months:
Dec 2025 $90.1k, Jan 2026 $75.6k, Feb $63.9k, Mar $59.0k, Apr $41.3k,
May $40.9k, Jun $27.9k.

**Where it comes from (full-history deltas — attribution shifts, total DR
unchanged, net = $0.00):**

| ref_code | delta (USD) | share |
|---|---|---|
| 99 untagged | −691,180 | 78.1% |
| 128 Spark.lend | −116,768 | 13.2% |
| 1 | −38,797 | 4.4% |
| 0 | −25,221 | 2.8% |
| 1002 DeFiSaver | −10,282 | 1.2% |
| 1001 Summer.fi | −2,898 | 0.3% |
| **1003 CowSwap** | **+885,145** | |

No same-tx conflicts between real Referrals and pseudo-referrals occurred
(0 overrides). Tokens other than sUSDS-eth are untouched in phase 1.

**Mint-path canary**: delivery-based tagging cannot see a solver minting
sUSDS *straight* to the end user (0x0 → user with no settlement transfer).
History audit (Sep 2024 – Jun 2026): 25 net-positive mint recipients inside
delivery txs, **all 6 distinct wallets intermediary contracts** (Balancer V3
vault ×19, 1inch executor, Odos, solvers) retaining dust/inventory residue —
max kept 126 sUSDS, **zero end users, zero missed attribution** (the final
holders in those txs were tagged via the delivery edge as designed).
`synthetic_referrals` WARNs when an untagged mint recipient keeps more than
1 token, so a behaviour change by solvers surfaces in pipeline logs instead
of silently under-attributing. Residual blind spot: a settlement tx with
*no* delivery transfer at all (pure direct-mint) is invisible even to the
canary; catching those would need Trade-event / tx-entrypoint selection.

Amatsu's "cow" sUSDS payouts over Jul 2025–Apr 2026 total $471.7k vs our
$546.3k for the same months — same order, different methodology (their sUSDS
tracker starts Jul 2025 and is sticky; ours tags from Sep 2024, terminates on
any later attribution signal, and excludes forwarder contracts). Our
Sep 2024–Jun 2025 months total ~$270k, a period where Amatsu recorded cow
under "Legacy SKY+CLE" (farms) instead. Exact reconciliation is not the goal:
the single-stream methodology is *supposed* to differ where Amatsu
double-counts (see cohorts above).

## Extending to other aggregators (Paraswap, 1inch, 0x, Enso, …)

Implemented machinery (`template_ab.SyntheticProgram`): a program is
`(name, ref_code, delivery-contract set)`; tagging = received the token FROM a
program contract AND net token delta > 0 across the tx; real Referral wins;
programs terminate each other via last-wins. Adding an aggregator whose users
receive the token *directly from a fixed contract* (the CowSwap shape) is a
one-line registry entry.

On-chain reconnaissance (Ethereum, sUSDS referral events):

| Code | Program | What the events show |
|---|---|---|
| 1004 | Paraswap | all 926 events land on 4 routers: Augustus v5 `0xdef1…ee57`, v6.2 `0x6a00…1068`, Delta `0x0000…380a`, v6.1 `0x00c6…06e0`. Users get sUSDS as transfers out of these routers → **fixed-contract shape, same as CowSwap** |
| 4011 | 1inch | events land on per-solver **executor** contracts (`0x4c3c…a6e3` ×406, …); tx entrypoints are AggregationRouter v6 `0x1111…2a65` / Fusion settlement `0x2d83…aded`. Delivery contract varies per solver → **needs tx-entrypoint selection**, not a fixed delivery set |
| — | 0x | no code assigned in our data. ExchangeProxy `0xdef1…5eff` is legacy; current 0x **Settler** contracts rotate addresses per release → needs a maintained registry (or entrypoint discovery) |
| — | Enso | no code assigned in our data; routing goes through per-user shortcut proxies → needs address-set confirmation |

Per aggregator, the requirements are:

1. **A synthetic ref code** assigned by Sky/ops (1004 and 4011 effectively
   exist; 0x / Enso need codes) + the partner payout wallet.
2. **The delivery/entrypoint contract set, per chain, per version era**
   (routers get redeployed; 0x Settler rotates by design). For
   fixed-delivery aggregators (Paraswap) the existing `SyntheticProgram` is
   enough. For executor-mediated ones (1inch, likely 0x Settler, Enso) the
   selection must be **tx-entrypoint based**: one HyperSync *transaction*
   query (`tx.to ∈ routers`) to collect program txs, then the same
   net-positive-recipient rule over the already-fetched transfers. That is a
   small extension (`SyntheticProgram.entrypoints`), not a redesign. Caveat:
   contract-wallet/ERC-4337 users don't have `tx.to = router`; accepted gap
   or supplement with executor-set discovery.
3. **Eligibility window** (Atlas requires program start/termination dates).
4. **Payout-policy sign-off** — enabling a program *shifts* DR from
   99/untagged (and any prior partner code, going forward) to the aggregator.
   It never changes the total, but it changes who is paid; that's a product
   decision per program, not an engineering default.
5. **A reference series to validate against** (like Amatsu's "cow" monthly
   payouts for 1003), if the program is meant to reproduce an existing stream.
6. **Scope decision per token**: sUSDS eth ships first; PSM3-L2 sUSDS (swap
   inside a settlement tx), stUSDS and USDS farms (Amatsu tags 1003 across
   all three) need the same program applied per token — plus a cross-venue
   propagation decision for farm staking after an aggregator buy.

## Note on Amatsu parity

Amatsu pays 1003 ("cow", wallet `0x616d…0669`) ≈ $50–60k/month, $602k
cumulative — at a scale consistent with the broad signal (~1.2k wallets), not
the strict one. If Amatsu's tracker is sticky per wallet *without* the
single-stream guarantee, part of its 1003 payout stream double-counts against
its own 99/partner attributions (cohorts above). Reproducing Amatsu's totals
and paying correctly are different targets; the comparison workbook should
show 1003 next to the 99/128 deltas so the overlap is visible, not netted
silently.
