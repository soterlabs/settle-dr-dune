-- =============================================================================
-- sp* vault share -> USD value, per (day, token_symbol, blockchain)
-- -----------------------------------------------------------------------------
-- Reproduces Spark's query_5357785. FULLY TRANSPARENT: sources only public
-- decoded tables spark_protocol_{ethereum,avalanche_c}.sparkvault_evt_{deposit,
-- withdraw} and prices.{day,latest}. conversion_rate = assets/shares (last event
-- per day, forward-filled). usd_value = conversion_rate, except spETH which is
-- conversion_rate * WETH price.
--
-- Output columns: dt, blockchain, contract_address, token_symbol,
--                 conversion_rate, eth_price, usd_value
-- =============================================================================
with
    token_targets (blockchain, token_symbol, token_addr, start_date) as (
        values
            ('ethereum',    'spUSDC',  0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d, date '2025-10-01'),
            ('ethereum',    'spUSDT',  0xe2e7a17dFf93280dec073C995595155283e3C372, date '2025-10-01'),
            ('ethereum',    'spETH',   0xfE6eb3b609a7C8352A241f7F3A21CEA4e9209B8f, date '2025-10-01'),
            ('ethereum',    'spPYUSD', 0x80128DbB9f07b93DDE62A6daeadb69ED14a7D354, date '2025-12-01'),
            ('avalanche_c', 'spUSDC',  0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d, date '2025-10-08')
    ),

    date_constants as (
        select date '2025-10-01' as start_date, current_date as end_date
    ),

    spark_vault_realtime_rates_raw as (
        select 'ethereum' as blockchain, evt_block_time, evt_index, contract_address,
               cast(assets as double) / cast(shares as double) as conversion_rate
        from (
            select evt_block_time, evt_index, contract_address, assets, shares
            from spark_protocol_ethereum.sparkvault_evt_deposit
            cross join date_constants dc
            where shares > 0 and evt_block_time >= dc.start_date and evt_block_time <= dc.end_date + interval '1' day
            union all
            select evt_block_time, evt_index, contract_address, assets, shares
            from spark_protocol_ethereum.sparkvault_evt_withdraw
            cross join date_constants dc
            where shares > 0 and evt_block_time >= dc.start_date and evt_block_time <= dc.end_date + interval '1' day
        )
        union all
        select 'avalanche_c' as blockchain, evt_block_time, evt_index, contract_address,
               cast(assets as double) / cast(shares as double) as conversion_rate
        from (
            select evt_block_time, evt_index, contract_address, assets, shares
            from spark_protocol_avalanche_c.sparkvault_evt_deposit
            cross join date_constants dc
            where shares > 0 and evt_block_time >= dc.start_date and evt_block_time <= dc.end_date + interval '1' day
            union all
            select evt_block_time, evt_index, contract_address, assets, shares
            from spark_protocol_avalanche_c.sparkvault_evt_withdraw
            cross join date_constants dc
            where shares > 0 and evt_block_time >= dc.start_date and evt_block_time <= dc.end_date + interval '1' day
        )
    ),

    daily_last_rates as (
        select blockchain, date(evt_block_time) as dt, contract_address,
               max_by(conversion_rate, evt_block_time) as conversion_rate
        from spark_vault_realtime_rates_raw
        group by blockchain, date(evt_block_time), contract_address
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
            tt.blockchain,
            tt.token_addr as contract_address,
            tt.token_symbol,
            coalesce(
                dlr.conversion_rate,
                last_value(dlr.conversion_rate) ignore nulls
                    over (partition by tt.blockchain, tt.token_addr order by ds.dt rows between unbounded preceding and current row)
            ) as conversion_rate
        from date_series ds
        cross join token_targets tt
        left join daily_last_rates dlr
            on ds.dt = dlr.dt and tt.blockchain = dlr.blockchain and tt.token_addr = dlr.contract_address
        where ds.dt >= tt.start_date
    )

select
    cdr.dt,
    cdr.blockchain,
    cdr.contract_address,
    cdr.token_symbol,
    coalesce(cdr.conversion_rate, 1) as conversion_rate,
    coalesce(p.price, pl.price) as eth_price,
    case
        when cdr.token_symbol = 'spETH' then coalesce(cdr.conversion_rate, 1) * coalesce(p.price, pl.price, 1)
        else coalesce(cdr.conversion_rate, 1)
    end as usd_value
from complete_daily_rates cdr
left join prices.day p
    on date(p.timestamp) = cdr.dt
    and p.blockchain = 'ethereum'
    and p.contract_address = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2
    and cdr.token_symbol = 'spETH'
left join prices.latest pl
    on pl.blockchain = 'ethereum'
    and pl.contract_address = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2
    and cdr.token_symbol = 'spETH'
    and p.price is null
order by cdr.blockchain, cdr.contract_address, cdr.dt desc;
