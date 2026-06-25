-- =============================================================================
-- Diagnostic: WHICH wallets are tagged 1004 and how much TWA each contributes
-- =============================================================================
-- Same attribution + TWA pipeline as diag_susds_ref1004_monthly_dr.sql, but the
-- final output is PER WALLET (all-time) instead of monthly, so we can see:
--   * max_daily_susds   — the largest balance ever counted for that wallet
--                         (a pool/bridge/CEX/whale will stand out here)
--   * days_counted_1004 — how many days it was carried as 1004 (forward-fill to
--                         current_date is why a single big holder inflates totals)
--   * sum_susds_days    — its contribution to the monthly sum_twa (in sUSDS shares)
--
-- Check the top addresses on Etherscan: if they are the sUSDS contract, a DEX
-- pool (Curve/Uniswap sUSDS), a bridge, or a CEX, they are NOT 1004 end-users
-- and must be excluded (add to the `routers` / intermediary list).
--
-- sUSDS contract (Ethereum): 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD
-- =============================================================================
with
    susds_addr as (
        select 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD as addr
    ),

    txs_1004 as (
        select distinct evt_tx_hash
        from sky_ethereum.susds_evt_referral
        where referral = 1004
    ),

    routers as (
        select addr from (values
            (0x6a000f20005980200259b80c5102003040001068),
            (0xdef171fe48cf0115b1d80b88dc8eab59176fee57)
        ) as t(addr)
    ),

    net_per_user_1004 as (
        select evt_tx_hash, user_addr, sum(amount) as net_susds
        from (
            select tr.evt_tx_hash, tr."to" as user_addr,
                   cast(tr.value as double) / 1e18 as amount
            from sky_ethereum.susds_evt_transfer tr
            cross join susds_addr s
            where tr.contract_address = s.addr
              and tr.evt_tx_hash in (select evt_tx_hash from txs_1004)
              and tr."to"   != 0x0000000000000000000000000000000000000000
              and tr."to"   != tr."from"
            union all
            select tr.evt_tx_hash, tr."from",
                   -cast(tr.value as double) / 1e18
            from sky_ethereum.susds_evt_transfer tr
            cross join susds_addr s
            where tr.contract_address = s.addr
              and tr.evt_tx_hash in (select evt_tx_hash from txs_1004)
              and tr."from" != 0x0000000000000000000000000000000000000000
              and tr."from" != tr."to"
        ) t
        group by evt_tx_hash, user_addr
        having sum(amount) > 0.0001
    ),

    wallets_1004 as (
        select distinct user_addr
        from net_per_user_1004
        where user_addr not in (select addr from routers)
    ),

    latest_ref_per_tx as (
        select evt_tx_hash, owner as user_addr, referral as ref_code
        from (
            select evt_tx_hash, owner, referral,
                   row_number() over (
                       partition by evt_tx_hash, owner order by evt_index desc
                   ) as rn
            from sky_ethereum.susds_evt_referral
            where owner in (select user_addr from wallets_1004)
        ) t
        where rn = 1
    ),

    ref_fallback as (
        select evt_tx_hash, ref_code
        from (
            select evt_tx_hash, referral as ref_code,
                   row_number() over (
                       partition by evt_tx_hash order by evt_index desc
                   ) as rn
            from sky_ethereum.susds_evt_referral
        ) t
        where rn = 1
    ),

    raw_transfers as (
        select tr.evt_block_time as ts, tr.evt_block_number, tr.evt_index,
               tr."to" as user_addr,
               cast(tr.value as double) / 1e18 as amount_change,
               coalesce(lr.ref_code, fb.ref_code) as ref_code_on_tx
        from sky_ethereum.susds_evt_transfer tr
        cross join susds_addr s
        left join latest_ref_per_tx lr on tr.evt_tx_hash = lr.evt_tx_hash and tr."to" = lr.user_addr
        left join ref_fallback      fb on tr.evt_tx_hash = fb.evt_tx_hash and lr.ref_code is null
        where tr.contract_address = s.addr
          and tr."to" in (select user_addr from wallets_1004)
          and tr."to"   != 0x0000000000000000000000000000000000000000
          and tr."to"   != tr."from"
        union all
        select tr.evt_block_time, tr.evt_block_number, tr.evt_index,
               tr."from",
               -cast(tr.value as double) / 1e18,
               coalesce(lr.ref_code, fb.ref_code)
        from sky_ethereum.susds_evt_transfer tr
        cross join susds_addr s
        left join latest_ref_per_tx lr on tr.evt_tx_hash = lr.evt_tx_hash and tr."from" = lr.user_addr
        left join ref_fallback      fb on tr.evt_tx_hash = fb.evt_tx_hash and lr.ref_code is null
        where tr.contract_address = s.addr
          and tr."from" in (select user_addr from wallets_1004)
          and tr."from" != 0x0000000000000000000000000000000000000000
          and tr."from" != tr."to"
    ),

    running_balances as (
        select
            user_addr, ts, evt_block_number, evt_index,
            date(ts) as dt,
            sum(amount_change) over (
                partition by user_addr
                order by evt_block_number asc, evt_index asc
                rows unbounded preceding
            ) as running_balance,
            coalesce(
                last_value(ref_code_on_tx) ignore nulls over (
                    partition by user_addr
                    order by evt_block_number asc, evt_index asc
                    rows unbounded preceding
                ), -999999
            ) as current_ref_code
        from raw_transfers
    ),

    daily_end_balances as (
        select user_addr, dt,
               running_balance   as end_of_day_balance,
               current_ref_code  as end_of_day_ref_code
        from (
            select user_addr, dt, running_balance, current_ref_code,
                   row_number() over (
                       partition by user_addr, dt
                       order by evt_block_number desc, evt_index desc
                   ) as rn
            from running_balances
        ) t
        where rn = 1
    ),

    user_days as (
        select distinct user_addr, dt from running_balances
    ),

    daily_start_balances as (
        select
            ud.user_addr, ud.dt,
            coalesce(
                lag(deb.end_of_day_balance) over (partition by ud.user_addr order by ud.dt),
                0
            ) as start_of_day_balance,
            lag(deb.end_of_day_ref_code) over (
                partition by ud.user_addr order by ud.dt
            ) as start_of_day_ref_code
        from user_days ud
        left join daily_end_balances deb
            on ud.user_addr = deb.user_addr and ud.dt = deb.dt
    ),

    events_with_daily_start as (
        select user_addr, ts, evt_block_number, evt_index,
               running_balance, current_ref_code, dt
        from running_balances
        union all
        select user_addr,
               cast(dt as timestamp),
               0, -1,
               start_of_day_balance,
               coalesce(start_of_day_ref_code, -999999),
               dt
        from daily_start_balances
        where start_of_day_balance is not null
    ),

    events_with_duration as (
        select user_addr, ts, dt, evt_block_number, evt_index,
               running_balance, current_ref_code,
               date_diff('second', ts,
                   coalesce(
                       lead(ts) over (
                           partition by user_addr, dt
                           order by evt_block_number asc, evt_index asc
                       ),
                       dt + interval '1' day
                   )
               ) as duration_seconds
        from events_with_daily_start
    ),

    daily_ref_segments as (
        select user_addr, dt, current_ref_code,
               sum(running_balance * duration_seconds) / 86400.0 as twa_balance
        from events_with_duration
        where date(ts) = dt
        group by user_addr, dt, current_ref_code
    ),

    user_final_balance as (
        select user_addr, end_of_day_balance as final_balance
        from (
            select user_addr, dt, end_of_day_balance,
                   row_number() over (partition by user_addr order by dt desc) as rn
            from daily_end_balances
        ) t
        where rn = 1
    ),

    user_date_ranges as (
        select deb.user_addr,
               min(deb.dt) as first_dt,
               case when ufb.final_balance > 1e-9
                    then greatest(max(deb.dt), current_date)
                    else max(deb.dt)
               end as last_dt
        from daily_end_balances deb
        join user_final_balance ufb on deb.user_addr = ufb.user_addr
        group by deb.user_addr, ufb.final_balance
    ),

    date_spine as (
        select u.user_addr, d.dt
        from user_date_ranges u
        cross join unnest(sequence(u.first_dt, u.last_dt, interval '1' day)) as d(dt)
    ),

    complete_daily_all as (
        select
            sp.user_addr,
            sp.dt,
            case
                when seg.dt is not null
                    then seg.twa_balance
                when drs_any.dt is not null
                    then 0.0
                else
                    last_value(deb.end_of_day_balance) ignore nulls over (
                        partition by sp.user_addr
                        order by sp.dt
                        rows unbounded preceding
                    )
            end as twa_balance,
            coalesce(
                last_value(deb.end_of_day_ref_code) ignore nulls over (
                    partition by sp.user_addr
                    order by sp.dt
                    rows unbounded preceding
                ),
                -999999
            ) as forwarded_ref_code
        from date_spine sp
        left join (select distinct user_addr, dt from daily_ref_segments) drs_any
            on sp.user_addr = drs_any.user_addr and sp.dt = drs_any.dt
        left join daily_ref_segments seg
            on sp.user_addr = seg.user_addr and sp.dt = seg.dt
           and seg.current_ref_code = 1004
        left join daily_end_balances deb
            on sp.user_addr = deb.user_addr and sp.dt = deb.dt
    ),

    complete_daily as (
        select user_addr, dt, twa_balance
        from complete_daily_all
        where forwarded_ref_code = 1004
          and twa_balance > 0
    ),

    -- Total net sUSDS each wallet actually received via Paraswap (1004) deposits,
    -- to contrast with how much balance is being counted for it.
    paraswap_received as (
        select user_addr, sum(net_susds) as total_paraswap_susds
        from net_per_user_1004
        where user_addr not in (select addr from routers)
        group by user_addr
    )

select
    cd.user_addr,
    count(*)                              as days_counted_1004,
    round(min(cd.twa_balance), 2)         as min_daily_susds,
    round(avg(cd.twa_balance), 2)         as avg_daily_susds,
    round(max(cd.twa_balance), 2)         as max_daily_susds,
    round(sum(cd.twa_balance), 2)         as sum_susds_days,
    round(pr.total_paraswap_susds, 2)     as total_paraswap_received,
    case when cd.user_addr = 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD
         then 'SUSDS_CONTRACT' else '' end as flag
from complete_daily cd
left join paraswap_received pr on pr.user_addr = cd.user_addr
group by cd.user_addr, pr.total_paraswap_susds
order by sum_susds_days desc
limit 50
