-- =============================================================================
-- DIAGNOSTIC (daily USDS base) — L2 sUSDS via PSM3, Base excluded
-- -----------------------------------------------------------------------------
-- Per-source daily-USDS query (see diag_daily_usds_susds_susdc.sql for the split
-- rationale). References EXACTLY ONE foundational TWA query (query_7640318).
--
-- Reports the USDS BASE the DR reward is applied to (NOT the reward):
--   sum_over_users(daily TWA sUSDS shares) x sUSDS->USDS rate (query_7640323).
-- query_7640318 covers base/arbitrum/optimism/unichain; Base is excluded here,
-- leaving arbitrum + optimism + unichain.
--
-- Output: dt, source, usds_base   (one row per day)
-- =============================================================================
with
    bal as (
        select dt, sum(time_weighted_avg_balance) as shares
        from query_7640318
        where blockchain <> 'base'
        group by dt
    )
select
    b.dt,
    'psm3' as source,
    b.shares * coalesce(r.susds_conversion_rate, 1) as usds_base
from bal b
left join query_7640323 r on r.dt = b.dt
order by b.dt
