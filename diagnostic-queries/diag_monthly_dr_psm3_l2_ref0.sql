-- =============================================================================
-- DIAGNOSTIC — monthly DR accruing to Category C (PSM3 L2, ref_code = 0) wallets
-- =============================================================================
-- Category C: on L2s, sUSDS is acquired via PSM3 `Swap(referralCode)`. When
-- no referral is supplied the contract emits referralCode = 0. This "default
-- untagged" population is large and legitimate; it is kept as ref_code = 0
-- (not remapped to 99) in the L2 pipeline, unlike Ethereum untagged sUSDS.
--
-- This query reproduces exactly the DR calculation our pipeline produces for
-- these wallets, without any deviation from the production methodology:
--   • TWA balance computed by the per-chain PSM3 monthly queries (with the
--     same referral forward-fill, idle-day balance fill, exclusion list, and
--     XR-rate join as in production).
--   • sUSDS share → USDS conversion via query_7640323 (same as production).
--   • Rate table query_7640322, reward_code = 'XR' (same as production).
--
-- Sources (all three non-Base L2 PSM3 monthly queries):
--   query_7647197 — psm3_arbitrum (dr_rewards_monthly_psm3_arbitrum.sql)
--   query_7647198 — psm3_optimism (dr_rewards_monthly_psm3_optimism.sql)
--   query_7647199 — psm3_unichain (dr_rewards_monthly_psm3_unichain.sql)
--
-- Base is intentionally excluded: its full-history per-user reconstruction
-- times out and is served as 8 windowed quarterly queries in production
-- (7684981–7684988). Those windows do not expose a ref_code column at the
-- stored-result level, making a clean ref_code=0 filter impractical. The
-- three chains above run as single queries and are directly filterable.
--
-- Output grain: (month, blockchain) — one row per calendar month per chain.
-- =============================================================================

with
    psm3_l2_all as (
        -- arbitrum
        select month, blockchain, token, ref_code, dr_usd, avg_twa_balance
        from query_7647197
        union all
        -- optimism
        select month, blockchain, token, ref_code, dr_usd, avg_twa_balance
        from query_7647198
        union all
        -- unichain
        select month, blockchain, token, ref_code, dr_usd, avg_twa_balance
        from query_7647199
    ),

    ref0_only as (
        select *
        from psm3_l2_all
        where ref_code = 0
    )

select
    month,
    blockchain,
    token,
    ref_code,
    sum(dr_usd)            as dr_usd,
    avg(avg_twa_balance)   as avg_twa_balance_sUSDS
from ref0_only
group by 1, 2, 3, 4
order by month, blockchain;
