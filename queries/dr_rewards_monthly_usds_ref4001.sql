-- =============================================================================
-- DR revenue (MONTHLY) — USDS held at 0x1e1D42781FC170EF9da004Fb735f56F0276d01B8
--                         (Ethereum, synthetic ref_code 4001)
-- =============================================================================
-- Tracks the USDS balance held by the contract at the address above and applies
-- the XR reward rate (same family as USDS-SKY / USDS-SPK / Aave USDS).
--
-- No share→USD conversion needed: USDS is already denominated in USD (1:1).
-- No referral attribution: the entire balance is assigned a single synthetic
-- ref_code 4001 (not an on-chain Referral event code; chosen to avoid collision
-- with all real codes and other synthetic codes in the pipeline).
--
-- Daily balance = end-of-day USDS balance of the contract, forward-filled on
-- days with no on-chain activity.
--
-- Rate: XR (Accessibility Rewards, USDS/sUSDS family)
--   0.6% APY  2024–2025  → reward_per = 365 × (exp(ln(1.006)/365) − 1)
--   0.5% APY  2026+      → reward_per = 365 × (exp(ln(1.005)/365) − 1)
--
-- Formula:  usds_balance / 365 × reward_per = dr_usd / day
--
-- Addresses:
--   USDS contract   0xdC035D45d973E3EC169d2276DDab16f1e407384F
--   Tracked holder  0x1e1D42781FC170EF9da004Fb735f56F0276d01B8
--
-- Output columns match every other monthly DR source so combine-dr-results.ts
-- can aggregate without schema changes:
--   month | blockchain | token | ref_code | dr_usd | avg_twa_balance
-- =============================================================================
with
    -- =========================================================================
    -- 1. Signed USDS flow into / out of the tracked contract
    -- =========================================================================
    raw_events as (
        select evt_block_time, evt_block_number, evt_index,
               cast(value as double) / 1e18 as amount
        from sky_ethereum.usds_evt_transfer
        where "to" = 0x1e1D42781FC170EF9da004Fb735f56F0276d01B8

        union all

        select evt_block_time, evt_block_number, evt_index,
               -cast(value as double) / 1e18
        from sky_ethereum.usds_evt_transfer
        where "from" = 0x1e1D42781FC170EF9da004Fb735f56F0276d01B8
    ),

    -- =========================================================================
    -- 2. Running balance, then end-of-day snapshot
    -- =========================================================================
    running as (
        select evt_block_time, evt_block_number, evt_index,
               date(evt_block_time) as dt,
               sum(amount) over (
                   order by evt_block_number asc, evt_index asc
                   rows unbounded preceding
               ) as balance
        from raw_events
    ),

    daily_end as (
        select dt, balance as eod_balance
        from (
            select dt, balance,
                   row_number() over (
                       partition by dt
                       order by evt_block_number desc, evt_index desc
                   ) as rn
            from running
        ) t
        where rn = 1
    ),

    -- =========================================================================
    -- 3. Calendar spine — forward-fill balance on days with no activity
    -- =========================================================================
    spine as (
        select dt
        from unnest(sequence(
            date '2024-09-01',
            current_date,
            interval '1' day
        )) t(dt)
    ),

    daily as (
        select s.dt,
               coalesce(
                   last_value(e.eod_balance) ignore nulls over (
                       order by s.dt
                       rows unbounded preceding
                   ),
                   0
               ) as usds_balance
        from spine s
        left join daily_end e on s.dt = e.dt
    ),

    -- =========================================================================
    -- 4. XR rate schedule (inlined from rates_dr.sql)
    --    reward_per = 365 × (exp(ln(1 + apy) / 365) − 1)
    -- =========================================================================
    xr_rates (start_dt, end_dt, apy, reward_per) as (
        values
            (date '2024-01-01', date '2025-12-31',
             0.006,
             365.0 * (exp(ln(1.006) / 365.0) - 1.0)),
            (date '2026-01-01', date '2030-12-31',
             0.005,
             365.0 * (exp(ln(1.005) / 365.0) - 1.0))
    ),

    -- =========================================================================
    -- 5. Daily DR: usds_balance / 365 × reward_per  (no share conversion)
    -- =========================================================================
    daily_dr as (
        select d.dt,
               d.usds_balance,
               d.usds_balance / 365.0 * r.reward_per as dr_usd,
               r.apy
        from daily d
        join xr_rates r on d.dt between r.start_dt and r.end_dt
        where d.usds_balance > 0
    )

-- =========================================================================
-- 6. Monthly rollup — schema matches all other dr_rewards_monthly_*.sql
-- =========================================================================
select
    date_trunc('month', dt) as month,
    'ethereum'              as blockchain,
    'USDS'                  as token,
    4001                    as ref_code,
    sum(dr_usd)             as dr_usd,
    avg(usds_balance)       as avg_twa_balance
from daily_dr
group by 1
order by 1
