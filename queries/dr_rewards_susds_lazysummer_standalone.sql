-- =============================================================================
-- Standalone: Monthly DR rewards for USDS held by LazySummer's contract,
-- calculated from the raw USDS ERC20 balance of that address.
--
-- Purpose: verify whether Amatsu's sUSDS Farm amounts for ref_code 1016
-- (lazysummer) match a balance-based attribution (USDS held by the contract)
-- rather than the Referral-event-based attribution used in our main pipeline.
--
-- Tracked addr (LazySummer): 0x447BF9d1485ABDc4C1778025DfdfbE8b894C3796
-- USDS token contract:        0xdC035D45d973E3EC169d2276DDab16f1e407384F (18 dec)
-- Source table:               erc20_ethereum.evt_Transfer
--
-- Methodology: identical TWA tail to twa_susds_susdc_erc4626.sql.
-- Rate:        XR, two-period schedule from rates_dr.sql
--              (0.6 % APY 2024-2025 | 0.5 % APY 2026+)
-- Conversion:  none — USDS balance is already USD-denominated (rate = 1).
-- =============================================================================
with
    -- -------------------------------------------------------------------------
    -- 1. USDS transfers involving LazySummer's contract.
    --    Scan from 2024-09-01 (USDS launch) to capture any pre-comparison
    --    opening balance.
    -- -------------------------------------------------------------------------
    raw_transfers as (
        select
            evt_block_time   as ts,
            evt_block_number,
            evt_index,
            cast(value as double) / 1e18 as amount_change
        from erc20_ethereum.evt_Transfer
        where contract_address = 0xdC035D45d973E3EC169d2276DDab16f1e407384F
          and "to"   = 0x447BF9d1485ABDc4C1778025DfdfbE8b894C3796
          and "from" <> 0x447BF9d1485ABDc4C1778025DfdfbE8b894C3796
          and date(evt_block_time) >= date '2024-09-01'

        union all

        select
            evt_block_time,
            evt_block_number,
            evt_index,
            -cast(value as double) / 1e18
        from erc20_ethereum.evt_Transfer
        where contract_address = 0xdC035D45d973E3EC169d2276DDab16f1e407384F
          and "from" = 0x447BF9d1485ABDc4C1778025DfdfbE8b894C3796
          and "to"   <> 0x447BF9d1485ABDc4C1778025DfdfbE8b894C3796
          and date(evt_block_time) >= date '2024-09-01'
    ),

    -- =========================================================================
    -- TWA TAIL — identical algorithm to twa_susds_susdc_erc4626.sql
    -- =========================================================================

    running_balances as (
        select
            ts, evt_block_number, evt_index, date(ts) as dt,
            sum(amount_change) over (
                order by evt_block_number asc, evt_index asc
                rows unbounded preceding
            ) as running_balance
        from raw_transfers
    ),

    daily_end_balances as (
        select dt, running_balance as end_of_day_balance
        from (
            select dt, running_balance,
                   row_number() over (
                       partition by dt
                       order by evt_block_number desc, evt_index desc
                   ) as rn
            from running_balances
        ) t
        where rn = 1
    ),

    transaction_days as (
        select distinct dt from running_balances
    ),

    daily_start_balances as (
        select
            td.dt,
            coalesce(
                lag(deb.end_of_day_balance) over (order by td.dt),
                0
            ) as start_of_day_balance
        from transaction_days td
        left join daily_end_balances deb on td.dt = deb.dt
    ),

    events_with_start as (
        select ts, evt_block_number, evt_index, running_balance, dt
        from running_balances
        union all
        select
            cast(dt as timestamp) as ts,
            0                     as evt_block_number,
            -1                    as evt_index,
            start_of_day_balance  as running_balance,
            dt
        from daily_start_balances
        where start_of_day_balance is not null
    ),

    events_with_duration as (
        select ts, evt_block_number, evt_index, running_balance, dt,
               date_diff('second', ts,
                   coalesce(
                       lead(ts) over (partition by dt order by evt_block_number asc, evt_index asc),
                       dt + interval '1' day
                   )
               ) as duration_seconds
        from events_with_start
    ),

    daily_twa as (
        select dt,
               sum(running_balance * duration_seconds) / 86400.0 as twa_balance
        from events_with_duration
        where date(ts) = dt
        group by dt
    ),

    -- Date spine: first ever transfer through Apr 2026 (last Amatsu data point)
    date_spine as (
        select dt
        from unnest(sequence(
            (select min(dt) from running_balances),
            date '2026-04-30',
            interval '1' day
        )) as t(dt)
    ),

    complete_daily_twa as (
        select
            ds.dt,
            coalesce(
                dt_twa.twa_balance,
                last_value(deb.end_of_day_balance) ignore nulls over (
                    order by ds.dt rows unbounded preceding
                )
            ) as twa_balance
        from date_spine ds
        left join daily_twa          dt_twa on ds.dt = dt_twa.dt
        left join daily_end_balances deb    on ds.dt = deb.dt
    ),

    -- -------------------------------------------------------------------------
    -- 2. XR rate — two-period schedule (from rates_dr.sql)
    --    reward_per = 365 × (exp(ln(1+apy)/365) − 1)
    -- -------------------------------------------------------------------------
    xr_rates (start_dt, end_dt, reward_per) as (
        values
            (date '2024-01-01', date '2025-12-31',
             365.0 * (exp(ln(1.006) / 365.0) - 1.0)),   -- 0.6 % APY
            (date '2026-01-01', date '2030-12-31',
             365.0 * (exp(ln(1.005) / 365.0) - 1.0))    -- 0.5 % APY
    ),

    -- -------------------------------------------------------------------------
    -- 3. Daily DR
    --    Formula mirrors dr_rewards_monthly_farms.sql (USDS farms):
    --      twa_balance / 365 × reward_per
    --    No conversion rate — USDS is already USD-denominated.
    -- -------------------------------------------------------------------------
    daily_dr as (
        select
            t.dt,
            t.twa_balance,
            t.twa_balance / 365.0 * xr.reward_per as dr_usd
        from complete_daily_twa t
        join xr_rates xr on t.dt between xr.start_dt and xr.end_dt
        where t.twa_balance > 0
    )

select
    date_trunc('month', dt) as month,
    sum(dr_usd)             as dr_usd,
    avg(twa_balance)        as avg_twa_usds
from daily_dr
group by 1
order by 1
