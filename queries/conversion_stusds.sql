-- =============================================================================
-- stUSDS share -> USDS conversion rate, per day
-- -----------------------------------------------------------------------------
-- Reproduces Spark's query_5449435. FULLY TRANSPARENT: sources only the public
-- decoded tables sky_ethereum.stusds_evt_{deposit,withdraw}. Rate = assets/shares
-- at the last event of each day, forward-filled across gap days. Downstream join
-- is on `dt`.
-- =============================================================================
with
    date_constants as (
        select date '2025-08-25' as start_date, current_date as end_date
    ),

    stusds_realtime_rates_raw as (
        select
            evt_block_time,
            cast(assets as double) / cast(shares as double) as stusds_conversion_rate
        from (
            select evt_block_time, assets, shares
            from sky_ethereum.stusds_evt_deposit
            cross join date_constants dc
            where shares > 0
              and evt_block_time >= dc.start_date
              and evt_block_time <= dc.end_date + interval '1' day
            union all
            select evt_block_time, assets, shares
            from sky_ethereum.stusds_evt_withdraw
            cross join date_constants dc
            where shares > 0
              and evt_block_time >= dc.start_date
              and evt_block_time <= dc.end_date + interval '1' day
        )
    ),

    daily_last_rates as (
        select
            date(evt_block_time) as dt,
            max_by(stusds_conversion_rate, evt_block_time) as stusds_conversion_rate
        from stusds_realtime_rates_raw
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
                dlr.stusds_conversion_rate,
                last_value(dlr.stusds_conversion_rate) ignore nulls
                    over (order by ds.dt rows between unbounded preceding and current row)
            ) as stusds_conversion_rate
        from date_series ds
        left join daily_last_rates dlr on ds.dt = dlr.dt
    )

select
    dt,
    coalesce(stusds_conversion_rate, 1) as stusds_conversion_rate
from complete_daily_rates
order by dt desc;
