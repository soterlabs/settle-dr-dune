-- =============================================================================
-- DIAGNOSTIC (daily USDS base) — USDS held in Aave aEthUSDS (Ethereum)
-- -----------------------------------------------------------------------------
-- Per-source daily-USDS query (see diag_daily_usds_susds_susdc.sql for the split
-- rationale). Self-contained: it scans a SINGLE contract's USDS Transfer flow,
-- so it references no foundational query and runs comfortably on its own. Logic
-- mirrors dr_rewards_monthly_usds_aave.sql (end-of-day balance, forward-filled).
--
-- USDS is already USD-denominated (1:1), so no conversion is applied. Reports the
-- USDS BASE the DR reward is applied to.
--
-- Output: dt, source, usds_base   (one row per day)
-- =============================================================================
with
    running as (
        select evt_block_number, evt_index, date(evt_block_time) as dt,
               sum(amount) over (order by evt_block_number asc, evt_index asc rows unbounded preceding) as balance
        from (
            select evt_block_time, evt_block_number, evt_index, cast(value as double) / 1e18 as amount
            from sky_ethereum.usds_evt_transfer
            where "to" = 0x32a6268f9Ba3642Dda7892aDd74f1D34469A4259
            union all
            select evt_block_time, evt_block_number, evt_index, -cast(value as double) / 1e18
            from sky_ethereum.usds_evt_transfer
            where "from" = 0x32a6268f9Ba3642Dda7892aDd74f1D34469A4259
        ) e
    ),
    daily_end as (
        select dt, balance as eod_balance
        from (
            select dt, balance,
                   row_number() over (partition by dt order by evt_block_number desc, evt_index desc) as rn
            from running
        ) t
        where rn = 1
    ),
    spine as (
        select dt from unnest(sequence(date '2024-09-01', current_date, interval '1' day)) s(dt)
    ),
    daily as (
        select sp.dt,
               coalesce(
                   last_value(e.eod_balance) ignore nulls over (order by sp.dt rows unbounded preceding),
                   0
               ) as usds_base
        from spine sp
        left join daily_end e on e.dt = sp.dt
    )
select dt, 'usds_aave' as source, usds_base
from daily
order by dt
