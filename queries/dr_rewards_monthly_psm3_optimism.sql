-- =============================================================================
-- DR revenue (MONTHLY) — L2 sUSDS via PSM3, Optimism only
-- -----------------------------------------------------------------------------
-- Split from dr_rewards_monthly_psm3.sql (query_7646378) which timed out when
-- covering all 4 L2 chains in a single execution. Each chain runs as a separate
-- query so the per-query data volume fits within Dune's execution limit.
--
-- Grain: (month, blockchain, token, ref_code).
-- Untagged sUSDS -> 99 (mirrors query_5310067).
-- PERF: idle-day fill is capped at last-tx-day for users with ~0 final balance.
--
-- SAVED AS: query_7647198  (https://dune.com/queries/7647198)
-- =============================================================================
with
    psm3_addr  as (select 0xe0F9978b907853F354d79188A3dEfbD41978af62 as addr),
    token_addr as (select 0xb5B2dc7fd34C249F4be7fB1fCea07950784229e0 as addr),

    raw_referral_events as (
        select s.evt_block_number, s.evt_tx_hash, s.evt_index,
               s.receiver as user_addr,
               cast(s.referralCode as bigint) as ref_code
        from spark_protocol_optimism.psm3_evt_swap s
        cross join psm3_addr pa
        cross join token_addr ta
        where s.contract_address = pa.addr
          and s.assetOut = ta.addr
          and s.referralCode < 1000000000
          and s.evt_block_time >= date '2024-09-01'
          and s.evt_block_time < timestamp '{{end_date}}'
    ),

    latest_referral_per_tx as (
        select evt_tx_hash, user_addr, ref_code
        from (
            select evt_tx_hash, user_addr, ref_code,
                   row_number() over (
                       partition by evt_tx_hash, user_addr
                       order by evt_index desc
                   ) as rn
            from raw_referral_events
        ) r
        where rn = 1
    ),

    raw_transfers as (
        select tr.evt_block_time as ts, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."to" as user_addr,
               cast(tr.value as double) / 1e18 as amount_change,
               lr.ref_code
        from erc20_optimism.evt_Transfer tr
        cross join token_addr ta
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."to" = lr.user_addr
        where tr.contract_address = ta.addr
          and date(tr.evt_block_time) >= date '2024-09-01'
          and tr.evt_block_time < timestamp '{{end_date}}'
          and tr."to" != 0x0000000000000000000000000000000000000000
        union all
        select tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."from",
               -cast(tr.value as double) / 1e18,
               lr.ref_code
        from erc20_optimism.evt_Transfer tr
        cross join token_addr ta
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."from" = lr.user_addr
        where tr.contract_address = ta.addr
          and date(tr.evt_block_time) >= date '2024-09-01'
          and tr.evt_block_time < timestamp '{{end_date}}'
          and tr."from" != 0x0000000000000000000000000000000000000000
    ),

    running_balances as (
        select user_addr, date(ts) as dt, ts, evt_block_number, evt_tx_hash, evt_index,
               sum(amount_change) over (
                   partition by user_addr
                   order by evt_block_number, evt_index rows unbounded preceding
               ) as running_balance,
               coalesce(
                   last_value(ref_code) ignore nulls over (
                       partition by user_addr
                       order by evt_block_number, evt_index rows unbounded preceding
                   ), -999999
               ) as current_ref_code
        from raw_transfers
    ),

    daily_end_balances as (
        select user_addr, dt, running_balance as end_of_day_balance, current_ref_code as end_of_day_ref_code
        from (
            select user_addr, dt, running_balance, current_ref_code,
                   row_number() over (partition by user_addr, dt order by evt_block_number desc, evt_index desc) as rn
            from running_balances
        ) t where rn = 1
    ),

    user_days as (select distinct user_addr, dt from running_balances),

    daily_start_balances as (
        select ud.user_addr, ud.dt,
               coalesce(lag(deb.end_of_day_balance) over (partition by ud.user_addr order by ud.dt), 0) as start_of_day_balance,
               lag(deb.end_of_day_ref_code)         over (partition by ud.user_addr order by ud.dt)    as start_of_day_ref_code
        from user_days ud
        left join daily_end_balances deb on ud.user_addr = deb.user_addr and ud.dt = deb.dt
    ),

    all_events as (
        select user_addr, dt, ts, evt_block_number, evt_tx_hash, evt_index, running_balance, current_ref_code
        from running_balances
        union all
        select s.user_addr, s.dt, cast(s.dt as timestamp), 0,
               from_hex('0000000000000000000000000000000000000000000000000000000000000000'),
               -1, s.start_of_day_balance, coalesce(s.start_of_day_ref_code, -999999)
        from daily_start_balances s where s.start_of_day_balance is not null
    ),

    event_durations as (
        select user_addr, dt, current_ref_code, running_balance,
               date_diff('second', ts,
                   coalesce(lead(ts) over (partition by user_addr, dt order by evt_block_number, evt_index),
                            dt + interval '1' day)
               ) as duration_seconds
        from all_events
        where date(ts) = dt
    ),

    segments as (
        select user_addr, dt, current_ref_code,
               sum(running_balance * duration_seconds) / 86400.0 as segment_twa
        from event_durations
        group by user_addr, dt, current_ref_code
    ),

    user_final_balance as (
        select user_addr, end_of_day_balance as final_balance
        from (
            select user_addr, end_of_day_balance,
                   row_number() over (partition by user_addr order by dt desc) as rn
            from daily_end_balances
        ) t where rn = 1
    ),

    user_date_ranges as (
        select deb.user_addr,
               min(deb.dt) as first_dt,
               case when ufb.final_balance > 1e-9 then greatest(max(deb.dt), current_date) else max(deb.dt) end as last_dt
        from daily_end_balances deb
        join user_final_balance ufb on deb.user_addr = ufb.user_addr
        group by deb.user_addr, ufb.final_balance
    ),

    calendar as (
        select u.user_addr, d.dt
        from user_date_ranges u
        cross join unnest(sequence(u.first_dt, u.last_dt, interval '1' day)) as d(dt)
    ),

    twa_daily as (
        select
            c.user_addr, c.dt,
            coalesce(
                s.current_ref_code,
                last_value(deb.end_of_day_ref_code) ignore nulls over (partition by c.user_addr order by c.dt rows unbounded preceding),
                -999999
            ) as ref_code,
            coalesce(
                s.segment_twa,
                last_value(deb.end_of_day_balance) ignore nulls over (partition by c.user_addr order by c.dt rows unbounded preceding)
            ) as twa_balance
        from calendar c
        left join segments s on c.user_addr = s.user_addr and c.dt = s.dt
        left join daily_end_balances deb on c.user_addr = deb.user_addr and c.dt = deb.dt
    ),

    balances as (
        select dt,
               'optimism' as blockchain,
               'sUSDS' as token,
               case when ref_code = -999999 then 99 else ref_code end as ref_code,
               sum(twa_balance) as amount
        from twa_daily where twa_balance > 0
        group by 1, 3, 4
    ),

    accrued as (
        select b.dt, b.blockchain, b.token, b.ref_code, b.amount,
               b.amount / 365.0 * r.reward_per as tw_reward
        from balances b
        join query_7640322 r on r.reward_code = 'XR' and b.dt between r.start_dt and r.end_dt
    ),

    daily_usd as (
        select a.dt, a.blockchain, a.token, a.ref_code, a.amount,
               a.tw_reward * coalesce(cs.susds_conversion_rate, 1) as tw_reward_usd
        from accrued a
        left join query_7640323 cs on a.dt = cs.dt
    )

select
    date_trunc('month', dt) as month,
    blockchain, token, ref_code,
    sum(tw_reward_usd) as dr_usd,
    avg(amount) as avg_twa_balance
from daily_usd
group by 1, 2, 3, 4
order by month, ref_code
