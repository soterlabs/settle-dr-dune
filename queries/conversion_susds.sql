-- =============================================================================
-- sUSDS share -> USDS conversion rate, per day (used for sUSDS AND sUSDC)
-- -----------------------------------------------------------------------------
-- Reproduces Spark's query_5752873. FULLY TRANSPARENT: sources only the public
-- decoded tables sky_ethereum.susds_evt_{deposit,withdraw}. Rate = assets/shares
-- at the last event of each day, forward-filled across gap days.
--
-- NOTE (mirrors Spark): this Ethereum sUSDS rate is applied to sUSDS on ALL
-- chains and to sUSDC on all chains (sUSDC has no independent rate in Spark's
-- pipeline — see dr-query-analysis-v2.md §9.2.2). The downstream join is on `dt`
-- only.
-- =============================================================================
with
    date_constants as (
        select date '2024-09-04' as start_date, current_date as end_date
    ),

    susds_realtime_rates_raw as (
        select
            evt_block_time,
            cast(assets as double) / cast(shares as double) as susds_conversion_rate
        from (
            select evt_block_time, assets, shares
            from sky_ethereum.susds_evt_deposit
            cross join date_constants dc
            where shares > 0
              and evt_block_time >= dc.start_date
              and evt_block_time <= dc.end_date + interval '1' day
            union all
            select evt_block_time, assets, shares
            from sky_ethereum.susds_evt_withdraw
            cross join date_constants dc
            where shares > 0
              and evt_block_time >= dc.start_date
              and evt_block_time <= dc.end_date + interval '1' day
        )
    ),

    daily_last_rates as (
        select
            date(evt_block_time) as dt,
            max_by(susds_conversion_rate, evt_block_time) as susds_conversion_rate
        from susds_realtime_rates_raw
        group by date(evt_block_time)
    ),

    date_series as (
        select dt
        from unnest(sequence(
            (select start_date from date_constants),
            (select end_date from date_constants),
            interval '1' day
        )) as t(dt)
    ),

    complete_daily_rates as (
        select
            ds.dt,
            coalesce(
                dlr.susds_conversion_rate,
                last_value(dlr.susds_conversion_rate) ignore nulls
                    over (order by ds.dt rows between unbounded preceding and current row)
            ) as susds_conversion_rate
        from date_series ds
        left join daily_last_rates dlr on ds.dt = dlr.dt
    )

select
    dt,
    coalesce(susds_conversion_rate, 1) as susds_conversion_rate
from complete_daily_rates
order by dt desc;
