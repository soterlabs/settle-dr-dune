-- =============================================================================
-- DIAGNOSTIC (daily USDS base) — sUSDS + sUSDC, Base excluded
-- -----------------------------------------------------------------------------
-- One of SIX per-source daily-USDS queries that together feed the double-count
-- diagnostic. Like the monthly DR queries, this references EXACTLY ONE
-- foundational TWA query (query_7877542) so the plan stays under Dune's stage
-- limit — combining all sources in a single query inlines every foundational
-- query at once and fails with "too many stages". Combine the six outputs
-- client-side with src/scripts/combine-daily-usds.ts.
--
-- Reports the USDS BASE the DR reward is applied to (NOT the reward itself):
--   sum_over_users(daily TWA sUSDS/sUSDC shares) x sUSDS->USDS rate (query_7877548).
-- Both sUSDS and sUSDC are priced with the sUSDS conversion rate (Spark has no
-- independent sUSDC rate). Base chain is excluded per request.
--
-- Output: dt, source, usds_base   (one row per day)
-- =============================================================================
with
    bal as (
        select dt, sum(time_weighted_avg_balance) as shares
        from query_7877542
        where blockchain <> 'base'
        group by dt
    )
select
    b.dt,
    'susds_susdc' as source,
    b.shares * coalesce(r.susds_conversion_rate, 1) as usds_base
from bal b
left join query_7877548 r on r.dt = b.dt
order by b.dt
