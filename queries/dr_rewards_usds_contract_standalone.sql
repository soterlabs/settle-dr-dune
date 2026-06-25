-- =============================================================================
-- Standalone (temp): Monthly DR rewards for USDS held by a specific address
-- on Ethereum, 2026-01-01 through 2026-05-31.
--
-- USDS token:   0xdC035D45d973E3EC169d2276DDab16f1e407384F  (18 decimals)
-- Tracked addr: 0x1e1D42781FC170EF9da004Fb735f56F0276d01B8
-- Treated as a single tagged holder; no referral attribution needed.
--
-- Methodology:  identical TWA tail to twa_susds_susdc_erc4626.sql.
--               XR rate (APY 0.5 % for 2026) inlined from rates_dr.sql.
--               No share->USDS conversion (USDS balance == USD balance).
--
-- Source table: erc20_ethereum.evt_Transfer — the generic decoded ERC20
--               transfer table on Dune.  If sky_ethereum.usds_evt_transfer
--               becomes available, substitute it for better scan performance.
-- =============================================================================
with
    -- -------------------------------------------------------------------------
    -- 1. Raw balance changes for the USDS token at the tracked address.
    --    Scan from 2024-09-01 (USDS launch) so any pre-2026 opening balance is
    --    correctly captured; upper bound 2026-06-01 (exclusive) keeps it lean.
    -- -------------------------------------------------------------------------
    raw_transfers as (
        select
            evt_block_time   as ts,
            evt_block_number,
            evt_index,
            cast(value as double) / 1e18 as amount_change
        from erc20_ethereum.evt_Transfer
        where contract_address = 0xdC035D45d973E3EC169d2276DDab16f1e407384F
          and "to"   = 0x1e1D42781FC170EF9da004Fb735f56F0276d01B8
          and "from" <> 0x1e1D42781FC170EF9da004Fb735f56F0276d01B8
          and date(evt_block_time) >= date '2024-09-01'
          and evt_block_time       <  timestamp '2026-06-01'

        union all

        select
            evt_block_time,
            evt_block_number,
            evt_index,
            -cast(value as double) / 1e18
        from erc20_ethereum.evt_Transfer
        where contract_address = 0xdC035D45d973E3EC169d2276DDab16f1e407384F
          and "from" = 0x1e1D42781FC170EF9da004Fb735f56F0276d01B8
          and "to"   <> 0x1e1D42781FC170EF9da004Fb735f56F0276d01B8
          and date(evt_block_time) >= date '2024-09-01'
          and evt_block_time       <  timestamp '2026-06-01'
    ),

    -- =========================================================================
    -- TWA TAIL — mirrors twa_susds_susdc_erc4626.sql exactly (single address,
    -- so no per-user partition key is needed).
    -- =========================================================================

    running_balances as (
        select
            ts,
            evt_block_number,
            evt_index,
            date(ts) as dt,
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

    -- Start-of-day balance = previous day's end-of-day balance (0 on first day)
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

    -- Merge real events with synthetic start-of-day markers
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

    -- Duration each balance level held within the day
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

    -- Time-weighted average balance for days that had transactions
    daily_twa as (
        select dt,
               sum(running_balance * duration_seconds) / 86400.0 as twa_balance
        from events_with_duration
        where date(ts) = dt
        group by dt
    ),

    -- Date spine: from first ever transfer to 2026-05-31
    date_spine as (
        select dt
        from unnest(sequence(
            (select min(dt) from running_balances),
            date '2026-05-31',
            interval '1' day
        )) as t(dt)
    ),

    -- Forward-fill: transaction days use daily_twa; no-transaction days carry
    -- forward the last end-of-day balance (constant holding)
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

    -- Restrict to 2026 Jan–May, drop days with zero/null balance
    period_twa as (
        select dt, twa_balance
        from complete_daily_twa
        where dt >= date '2026-01-01'
          and twa_balance > 0
    ),

    -- -------------------------------------------------------------------------
    -- DR reward calculation
    -- XR rate 2026: APY = 0.005
    -- Formula (from rates_dr.sql): 365 × (exp(ln(1+apy)/365) − 1)
    -- Applied daily: twa_balance / 365 × reward_per  (same as dr_rewards_monthly_farms.sql)
    -- No conversion rate: USDS is already denominated in USD (rate = 1).
    -- -------------------------------------------------------------------------
    daily_dr as (
        select
            dt,
            twa_balance,
            twa_balance / 365.0 * (365.0 * (exp(ln(1.005) / 365.0) - 1.0)) as dr_usd
        from period_twa
    )

select
    date_trunc('month', dt) as month,
    'ethereum'              as blockchain,
    'USDS'                  as token,
    sum(dr_usd)             as dr_usd,
    avg(twa_balance)        as avg_twa_balance
from daily_dr
group by 1, 2, 3
order by 1
