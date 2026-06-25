-- =============================================================================
-- DIAGNOSTIC (daily USDS base) — Spark sp* vaults (spUSDC/spUSDT/spPYUSD)
-- -----------------------------------------------------------------------------
-- Per-source daily-USDS query (see diag_daily_usds_susds_susdc.sql for the split
-- rationale). References the foundational TWA query (query_7640321) plus the two
-- small SP conversion helpers — the SAME set the monthly SP query inlines, which
-- runs within the stage limit.
--
-- Reports the USDS BASE the DR reward is applied to (NOT the reward):
--   sum_over_users( daily TWA sp* shares
--                   x deployment ratio (query_7683727)
--                   x share->USD value (query_7640325) )
-- spETH is excluded: it is ETH-denominated (not USDS) and earns zero DR.
--
-- Output: dt, source, usds_base   (one row per day)
-- =============================================================================
select
    b.dt,
    'sp' as source,
    sum(
        b.time_weighted_avg_balance
        * coalesce(dr.deployment_ratio, 0)
        * coalesce(csp.usd_value, 1)
    ) as usds_base
from query_7640321 b
left join query_7683727 dr
    on dr.dt = b.dt and dr.blockchain = b.blockchain and dr.vault_symbol = b.symbol
left join query_7640325 csp
    on csp.dt = b.dt and csp.token_symbol = b.symbol and csp.blockchain = b.blockchain
where b.symbol <> 'spETH'
group by b.dt
order by b.dt
