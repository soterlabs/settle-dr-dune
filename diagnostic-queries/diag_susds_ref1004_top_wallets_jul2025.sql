-- =============================================================================
-- Diagnostic: TOP ref_code 1004 (Paraswap) wallets by avg DR-eligible balance
--             (EOA-filtered recipient attribution) — JULY 2025 FORWARD ONLY
-- =============================================================================
-- Same pipeline as diag_susds_ref1004_monthly_dr_eoa.sql, but instead of a
-- monthly rollup it aggregates PER WALLET over dt >= 2025-07-01:
--   * eligible_days        — # days the wallet was counted as 1004 (twa_balance>0)
--   * avg_eligible_twa_usds — average DR-eligible 1004 balance (USD) across those days
--   * max_eligible_twa_usds — largest single-day eligible balance (spots whales)
--   * est_total_dr_usd      — estimated total DR payout to the wallet (Jul 2025+)
-- Sorted by avg_eligible_twa_usds desc so the biggest contributors surface first.
--   (avg is over days the wallet actually held a 1004 balance, not all calendar days.)
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

    -- CowSwap settlement txs (GPv2Settlement Trade events). A tracked wallet that
    -- RECEIVES sUSDS inside one of these is acquiring via CowSwap, which in the
    -- integrated pipeline is synthetic ref 1003 and OVERWRITES a prior 1004 tag
    -- (last-referral-wins). We inject 1003 on those receive legs so the wallet
    -- stops counting as 1004 from that CowSwap acquisition onward.
    cowswap_txs as (
        select distinct evt_tx_hash
        from gnosis_protocol_v2_ethereum.gpv2settlement_evt_trade
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
        select distinct n.user_addr
        from net_per_user_1004 n
        left join ethereum.contracts c on c.address = n.user_addr
        where c.address is null          -- keep only EOAs (no deployed code)
    ),

    latest_ref_per_tx as (
        select evt_tx_hash, owner as user_addr, referral as ref_code
        from (
            select evt_tx_hash, owner, referral,
                   row_number() over (
                       partition by evt_tx_hash, owner
                       order by evt_index desc
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
                       partition by evt_tx_hash
                       order by evt_index desc
                   ) as rn
            from sky_ethereum.susds_evt_referral
        ) t
        where rn = 1
    ),

    raw_transfers as (
        select tr.evt_block_time as ts, tr.evt_block_number, tr.evt_index,
               tr."to" as user_addr,
               cast(tr.value as double) / 1e18 as amount_change,
               coalesce(lr.ref_code, fb.ref_code,
                        case when cs.evt_tx_hash is not null then 1003 end) as ref_code_on_tx
        from sky_ethereum.susds_evt_transfer tr
        cross join susds_addr s
        left join latest_ref_per_tx lr on tr.evt_tx_hash = lr.evt_tx_hash and tr."to" = lr.user_addr
        left join ref_fallback      fb on tr.evt_tx_hash = fb.evt_tx_hash and lr.ref_code is null
        left join cowswap_txs       cs on tr.evt_tx_hash = cs.evt_tx_hash
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

    -- Eligible days only, restricted to July 2025 forward
    complete_daily as (
        select user_addr, dt, twa_balance
        from complete_daily_all
        where forwarded_ref_code = 1004
          and twa_balance > 0
          and dt >= date '2025-07-01'
    ),

    xr_rates (start_dt, end_dt, apy, reward_per) as (
        values
            (date '2024-01-01', date '2025-12-31',
             0.004,
             365.0 * (exp(ln(1.004) / 365.0) - 1.0)),
            (date '2026-01-01', date '2030-12-31',
             0.005,
             365.0 * (exp(ln(1.005) / 365.0) - 1.0))
    ),

    susds_raw_rates as (
        select evt_block_time,
               cast(assets as double) / cast(shares as double) as rate
        from sky_ethereum.susds_evt_deposit
        where shares > 0
        union all
        select evt_block_time,
               cast(assets as double) / cast(shares as double)
        from sky_ethereum.susds_evt_withdraw
        where shares > 0
    ),

    daily_conversion_raw as (
        select date(evt_block_time) as dt,
               max_by(rate, evt_block_time) as rate
        from susds_raw_rates
        group by date(evt_block_time)
    ),

    conversion_spine as (
        select dt
        from unnest(sequence(
            date '2024-09-04',
            current_date,
            interval '1' day
        )) as t(dt)
    ),

    daily_conversion as (
        select s.dt,
               coalesce(
                   r.rate,
                   last_value(r.rate) ignore nulls over (
                       order by s.dt rows unbounded preceding
                   ),
                   1.0
               ) as susds_rate
        from conversion_spine s
        left join daily_conversion_raw r on s.dt = r.dt
    ),

    daily_dr as (
        select
            c.user_addr,
            c.dt,
            c.twa_balance * conv.susds_rate                            as twa_usds,
            c.twa_balance / 365.0 * xr.reward_per * conv.susds_rate   as dr_usd
        from complete_daily c
        join xr_rates       xr   on c.dt between xr.start_dt and xr.end_dt
        join daily_conversion conv on c.dt = conv.dt
    )

-- =========================================================================
-- Per-wallet aggregation (July 2025 forward), sorted by avg eligible balance
-- =========================================================================
select
    user_addr,
    count(*)                          as eligible_days,
    round(avg(twa_usds), 2)           as avg_eligible_twa_usds,
    round(max(twa_usds), 2)           as max_eligible_twa_usds,
    round(sum(dr_usd),   2)           as est_total_dr_usd
from daily_dr
group by user_addr
order by avg_eligible_twa_usds desc
limit 50
