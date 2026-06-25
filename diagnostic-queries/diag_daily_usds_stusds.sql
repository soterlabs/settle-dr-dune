-- =============================================================================
-- DIAGNOSTIC (daily USDS base) — stUSDS (Ethereum)
-- -----------------------------------------------------------------------------
-- Per-source daily-USDS query (see diag_daily_usds_susds_susdc.sql for the split
-- rationale). References EXACTLY ONE foundational TWA query (query_7640319).
--
-- Reports the USDS BASE the DR reward is applied to (NOT the reward):
--   sum_over_users(daily TWA stUSDS shares) x stUSDS->USDS rate (query_7640324).
--
-- Output: dt, source, usds_base   (one row per day)
-- =============================================================================
with
    bal as (
        select dt, sum(time_weighted_avg_balance) as shares
        from query_7640319
        group by dt
    )
select
    b.dt,
    'stusds' as source,
    b.shares * coalesce(r.stusds_conversion_rate, 1) as usds_base
from bal b
left join query_7640324 r on r.dt = b.dt
order by b.dt
