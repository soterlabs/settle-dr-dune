-- =============================================================================
-- Layer 2 + 3: Daily Distribution Rewards (DR) revenue, in USD
-- -----------------------------------------------------------------------------
-- Mirrors Spark's daily rewards engine (raw-queries/xr-ar-rewards-daily-raw.txt)
-- but built entirely on OUR self-owned, transparent inputs — no dependency on
-- Spark's opaque dune.sparkdotfi.result_spark_* datasets.
--
-- Pipeline:
--   Layer 0/1 (per-user TWA balance + ref_code)   <- the 5 twa_*.sql queries
--   Layer 2   (sum away user -> per dt/chain/token/ref_code)   <- `balances` CTE
--   Layer 3a  (x reward rate by token+date)                    <- rates_dr.sql
--   Layer 3b  (x share->USD conversion)                        <- conversion_*.sql
--   => tw_reward_usd : daily DR revenue per (dt, chain, token, ref_code)
--
-- Output is general (every ref_code). Filter/aggregate downstream (see
-- dr_rewards_rollup.sql) for a specific code or an all-time-per-code total.
--
-- -----------------------------------------------------------------------------
-- !!!!!!!!!!!!!!!!!!!!!!!!!!  UNRESOLVED PLACEHOLDER  !!!!!!!!!!!!!!!!!!!!!!!!!!!!
-- sp* DEPLOYMENT RATIO is HARDCODED to 0.5 and is *deliberately wrong*.
-- Spark applies a per-day TWA deployment ratio (vault_deployed / vault_total)
-- sourced from an OPAQUE dataset (result_spark_savings_v_2_vaults_time_weighted
-- _average_holdings, via query_6398769). Amatsu uses a flat 0.9. We use 0.5 on
-- purpose so sp* (spUSDC/spUSDT/spPYUSD/spETH) numbers are obviously off and this
-- TODO cannot be silently shipped. REPLACE before trusting any sp* revenue.
-- See queries/README.md "Known placeholders / not-yet-implemented".
-- !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
--
-- WIRING — saved Dune query IDs (already substituted below; see README):
--   query_7640317  twa_susds_susdc_erc4626.sql   (sUSDS eth + sUSDC all chains)
--   query_7640318  twa_susds_psm3_l2.sql         (L2 sUSDS via PSM3)
--   query_7640319  twa_stusds.sql                (stUSDS)
--   query_7640320  twa_usds_staking_farms.sql    (USDS-SKY / USDS-SPK / USDS-CLE)
--   query_7640321  twa_sp_vaults.sql             (spUSDC/spUSDT/spPYUSD/spETH)
--   query_7640322  rates_dr.sql
--   query_7640323  conversion_susds.sql
--   query_7640324  conversion_stusds.sql
--   query_7640325  conversion_sp_vaults.sql
-- =============================================================================
with
    -- ----- sp* deployment-ratio placeholder (SEE BANNER ABOVE) -----
    params (sp_deployment_ratio) as (
        values (cast(0.5 as double))   -- TODO: replace 0.5 with real per-day ratio
    ),

    -- ----- Layer 2: union all per-user TWA balances, sum away user_addr -----
    raw_balances as (
        select blockchain, contract_address, symbol, dt, ref_code, time_weighted_avg_balance from query_7640317
        union all
        select blockchain, contract_address, symbol, dt, ref_code, time_weighted_avg_balance from query_7640318
        union all
        select blockchain, contract_address, symbol, dt, ref_code, time_weighted_avg_balance from query_7640319
        union all
        select blockchain, contract_address, symbol, dt, ref_code, time_weighted_avg_balance from query_7640320
        union all
        select blockchain, contract_address, symbol, dt, ref_code, time_weighted_avg_balance from query_7640321
    ),

    balances as (
        select
            dt,
            blockchain,
            symbol as token,
            -- Untagged reclassification, mirroring Spark's query_5310067:
            --   sUSDC untagged -> 127, sUSDS untagged -> 99. Others kept as-is.
            case
                when symbol = 'sUSDC' and ref_code = -999999 then 127
                when symbol = 'sUSDS' and ref_code = -999999 then 99
                else ref_code
            end as ref_code,
            sum(time_weighted_avg_balance) as amount
        from raw_balances
        group by 1, 2, 3, 4
    ),

    -- ----- token -> reward_code map (see rates_dr.sql) -----
    with_reward_code as (
        select
            b.*,
            case
                when b.token = 'stUSDS' then 'XR-stUSDS'
                when b.token in ('sUSDS', 'USDS-SKY', 'USDS-SPK', 'USDS-CLE') then 'XR'
                when b.token in ('sUSDC', 'spUSDC', 'spUSDT', 'spPYUSD', 'spETH') then 'XR*'
                else 'XR'
            end as reward_code
        from balances b
    ),

    -- ----- Layer 3a: apply reward rate (reward_base/365 * reward_per) -----
    -- For sp* tokens the rate applies to the DEPLOYED portion (amount * ratio);
    -- all other tokens accrue on the full amount.
    accrued as (
        select
            w.dt,
            w.blockchain,
            w.token,
            w.ref_code,
            w.amount,
            w.reward_code,
            r.reward_per,
            case when w.token like 'sp%' then w.amount * p.sp_deployment_ratio else w.amount end as reward_base,
            (case when w.token like 'sp%' then w.amount * p.sp_deployment_ratio else w.amount end)
                / 365.0 * r.reward_per as tw_reward
        from with_reward_code w
        cross join params p
        join query_7640322 r
            on r.reward_code = w.reward_code
            and w.dt between r.start_dt and r.end_dt
    )

-- ----- Layer 3b: convert token-denominated reward to USD -----
select
    a.dt,
    a.blockchain,
    a.token,
    a.ref_code,
    a.reward_code,
    a.amount,
    a.reward_base,
    a.reward_per,
    a.tw_reward,
    case
        when a.token in ('sUSDS', 'sUSDC') then cs.susds_conversion_rate
        when a.token = 'stUSDS' then ct.stusds_conversion_rate
        when a.token in ('spUSDC', 'spUSDT', 'spPYUSD', 'spETH') then csp.usd_value
        else 1
    end as price_usd,
    a.tw_reward * case
        when a.token in ('sUSDS', 'sUSDC') then cs.susds_conversion_rate
        when a.token = 'stUSDS' then ct.stusds_conversion_rate
        when a.token in ('spUSDC', 'spUSDT', 'spPYUSD', 'spETH') then csp.usd_value
        else 1
    end as tw_reward_usd
from accrued a
left join query_7640323 cs on a.dt = cs.dt
left join query_7640324 ct on a.dt = ct.dt
left join query_7640325 csp
    on a.dt = csp.dt and a.token = csp.token_symbol and a.blockchain = csp.blockchain
order by a.dt, a.blockchain, a.token, a.ref_code;
