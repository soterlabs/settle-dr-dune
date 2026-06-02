-- =============================================================================
-- Layer 4: DR revenue rollup per ref_code (all assets, all chains, all time)
-- -----------------------------------------------------------------------------
-- Aggregates dr_rewards_daily.sql to one row per ref_code (the headline number),
-- plus a per-(ref_code, token) breakdown at the bottom for drill-down.
--
-- WIRING: query_7640326 is the saved Dune query ID of dr_rewards_daily.sql.
--
-- Usage:
--   - All ref_codes, all time:      run as-is.
--   - A single code (e.g. 1):       add `where ref_code = 1` to the final selects.
--   - A month/range:                add `where dt >= date '...' and dt < ...`
--                                   to the `daily` CTE.
--
-- NOTE: sp* (spUSDC/spUSDT/spPYUSD/spETH) figures use the placeholder 0.5
-- deployment ratio from dr_rewards_daily.sql and are NOT trustworthy yet.
-- =============================================================================
with
    daily as (
        select dt, blockchain, token, ref_code, tw_reward_usd
        from query_7640326
    )

-- Headline: total DR revenue per ref_code across everything.
select
    ref_code,
    sum(tw_reward_usd) as total_dr_usd,
    count(distinct token) as n_tokens,
    count(distinct blockchain) as n_chains,
    min(dt) as first_day,
    max(dt) as last_day
from daily
group by ref_code
order by total_dr_usd desc;

-- -----------------------------------------------------------------------------
-- Per-(ref_code, token, chain) breakdown — uncomment to drill down instead:
--
-- select
--     ref_code,
--     token,
--     blockchain,
--     sum(tw_reward_usd) as dr_usd,
--     min(dt) as first_day,
--     max(dt) as last_day
-- from daily
-- group by ref_code, token, blockchain
-- order by ref_code, dr_usd desc;
-- =============================================================================
