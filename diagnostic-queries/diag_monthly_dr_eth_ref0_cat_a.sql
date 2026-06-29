-- =============================================================================
-- DIAGNOSTIC — monthly DR accruing to Category A (explicit Referral = 0) wallets
-- =============================================================================
-- Category A: a vault / farm / staking contract emits a dedicated on-chain
-- `Referral(referral = 0)` event. The depositing wallet is tagged ref_code = 0
-- and forward-filled. This is *always* a real on-chain zero — not a sentinel
-- or untagged fallback (those use -999999 → 99/127).
--
-- Source: query_7646377 (dr_rewards_monthly_susds_susdc.sql)
--   Covers sUSDS (ethereum) and sUSDC (ethereum + arbitrum / optimism / unichain).
--   Base sUSDC is excluded (blockchain != 'base') to keep scope clean.
--
-- Simplified from original design: stUSDS (7646379), farms (7646380), and
-- sp* (7683760) were removed because Dune's stage limit is hit when four
-- saved queries are referenced together (each inlines ~3 sub-queries). Those
-- sources contribute negligibly to ref_code = 0 DR and can be queried
-- independently if needed.
--
-- Output grain: (month, blockchain, token) — one row per chain+token per month.
-- =============================================================================

select
    month,
    blockchain,
    token,
    ref_code,
    dr_usd,
    avg_twa_balance
from query_7646377
where ref_code = 0
  and blockchain != 'base'
order by month, blockchain, token;
