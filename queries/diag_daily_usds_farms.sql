-- =============================================================================
-- DIAGNOSTIC (daily USDS base) — USDS staking farms (Sky / Spk / Chronicle, Ethereum)
-- -----------------------------------------------------------------------------
-- Per-source daily-USDS query (see diag_daily_usds_susds_susdc.sql for the split
-- rationale). References EXACTLY ONE foundational TWA query (query_7640320).
--
-- Balance is ALREADY in USDS (the farms stake raw USDS), so no share->USD
-- conversion is needed. Reports the USDS BASE the DR reward is applied to.
--
-- Output: dt, source, usds_base   (one row per day)
-- =============================================================================
select
    dt,
    'farms' as source,
    sum(time_weighted_avg_balance) as usds_base
from query_7640320
group by dt
order by dt
