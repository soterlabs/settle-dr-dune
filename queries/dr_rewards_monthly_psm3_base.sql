-- =============================================================================
-- DR revenue (MONTHLY) — L2 sUSDS via PSM3, Base only — WINDOWED
-- -----------------------------------------------------------------------------
-- Base has by far the most L2 sUSDS Transfer activity of the four PSM3 chains.
-- Reconstructing the full-history per-user daily TWA in a single execution
-- always blew past Dune's 30-minute limit (even on the `large` engine and even
-- isolated to Base). This version is WINDOWED so each run only materializes the
-- expensive per-(user, day) calendar for one [start_date, end_date) slice.
--
-- HOW THE SPLIT STAYS CORRECT (balances are path-dependent):
--   * `opening_balance` + `opening_ref` seed every user with their balance +
--     last ref_code as of start_date, computed from `prior_transfers` (all
--     transfers STRICTLY BEFORE the window, attributed exactly as the in-window
--     stream is). These are injected as a single synthetic event (block -1) at
--     start_date 00:00 in `raw_transfers`, so the in-window running balance
--     starts from the right place — a user who held sUSDS before the window
--     still accrues.
--   * The idle-day fill is CAPPED at (end_date - 1 day) so a window never bleeds
--     into the next one. For the final/most-recent window, end_date is today (or
--     later), so the cap naturally becomes current_date and ongoing holders keep
--     accruing on idle days, exactly as the un-windowed query did.
--   * Windows are disjoint in [start_date, end_date) and MUST align to month
--     boundaries (quarters do) so the monthly output grain is never split across
--     two windows. Union the per-window results client-side — see
--     src/scripts/run-psm3-base-windows.ts.
--
-- Grain: (month, blockchain, token, ref_code).
-- Untagged sUSDS -> 99 (mirrors query_5310067).
--
-- PARAMS:  {{start_date}}  inclusive window start (default 2024-09-01 = genesis)
--          {{end_date}}    exclusive window end
--
-- DEPLOYED AS: a set of public quarterly windows with dates baked in, query IDs
-- 7684981–7684988 (one per calendar quarter from 2024-09; see queries/README.md
-- for the window→URL table). Their union reproduces the full coverage of the
-- original query_7647196, which holds the un-windowed SQL that always times out
-- (owned by a different account). This file is the parameterized template each
-- window is generated from.
-- =============================================================================
with
    psm3_addr  as (select 0x1601843c5E9bC251A3272907010AFa41Fa18347E as addr),
    token_addr as (select 0x5875eEE11Cf8398102FdAd704C9E96607675467a as addr),

    -- All referral attributions from genesis up to the window end. We need the
    -- full prefix (not just the window) so a user's ref_code can be carried into
    -- the window from an earlier referral. The swap table is small, so this scan
    -- is cheap relative to the daily TWA expansion.
    raw_referral_events as (
        select s.evt_block_number, s.evt_block_time, s.evt_tx_hash, s.evt_index,
               s.receiver as user_addr,
               cast(s.referralCode as bigint) as ref_code
        from spark_protocol_base.psm3_evt_swap s
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

    -- Signed sUSDS balance deltas STRICTLY BEFORE the window, attributed exactly
    -- as the in-window stream is (ref_code from latest_referral_per_tx on the
    -- transfer's tx_hash + user). Used only to seed opening balance + ref; the
    -- per-day reconstruction itself uses window_transfers below.
    prior_transfers as (
        select tr.evt_block_number, tr.evt_index,
               tr."to" as user_addr,
               cast(tr.value as double) / 1e18 as amount_change,
               lr.ref_code
        from erc20_base.evt_Transfer tr
        cross join token_addr ta
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."to" = lr.user_addr
        where tr.contract_address = ta.addr
          and date(tr.evt_block_time) >= date '2024-09-01'
          and tr.evt_block_time < timestamp '{{start_date}}'
          and tr."to" != 0x0000000000000000000000000000000000000000
        union all
        select tr.evt_block_number, tr.evt_index,
               tr."from",
               -cast(tr.value as double) / 1e18,
               lr.ref_code
        from erc20_base.evt_Transfer tr
        cross join token_addr ta
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."from" = lr.user_addr
        where tr.contract_address = ta.addr
          and date(tr.evt_block_time) >= date '2024-09-01'
          and tr.evt_block_time < timestamp '{{start_date}}'
          and tr."from" != 0x0000000000000000000000000000000000000000
    ),

    -- Per-user opening balance as of start_date (sum of every prior delta).
    opening_balance as (
        select user_addr, sum(amount_change) as bal
        from prior_transfers
        group by user_addr
    ),

    -- Per-user ref_code carried into the window: the LAST non-null ref_code among
    -- the user's prior transfers, ordered by (block, index). This is exactly the
    -- value the un-windowed query's `last_value(ref_code) ignore nulls over
    -- running_balances` would hold entering start_date — derived from the same
    -- transfer+latest_referral_per_tx attribution, NOT from raw swap events
    -- (a swap only carries a ref into balance via a matching same-tx transfer).
    -- NULL if the user had no ref-bearing transfer before the window, so an
    -- in-window referral can still set it (untagged -> -999999 -> 99).
    opening_ref as (
        select user_addr, ref_code
        from (
            select user_addr, ref_code,
                   row_number() over (
                       partition by user_addr
                       order by evt_block_number desc, evt_index desc
                   ) as rn
            from prior_transfers
            where ref_code is not null
        ) t
        where rn = 1
    ),

    window_transfers as (
        -- incoming
        select tr.evt_block_time as ts, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."to" as user_addr,
               cast(tr.value as double) / 1e18 as amount_change,
               lr.ref_code
        from erc20_base.evt_Transfer tr
        cross join token_addr ta
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."to" = lr.user_addr
        where tr.contract_address = ta.addr
          and tr.evt_block_time >= timestamp '{{start_date}}'
          and tr.evt_block_time < timestamp '{{end_date}}'
          and tr."to" != 0x0000000000000000000000000000000000000000
        union all
        -- outgoing
        select tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."from",
               -cast(tr.value as double) / 1e18,
               lr.ref_code
        from erc20_base.evt_Transfer tr
        cross join token_addr ta
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."from" = lr.user_addr
        where tr.contract_address = ta.addr
          and tr.evt_block_time >= timestamp '{{start_date}}'
          and tr.evt_block_time < timestamp '{{end_date}}'
          and tr."from" != 0x0000000000000000000000000000000000000000
    ),

    -- Synthetic opening event (block -1 so it sorts before every real event of
    -- the window) carrying the seeded balance + ref_code at start_date 00:00.
    -- Emitted for every user with a non-zero opening balance OR any in-window
    -- activity, so idle holders that entered the window with a balance are kept.
    raw_transfers as (
        select ts, evt_block_number, evt_tx_hash, evt_index, user_addr, amount_change, ref_code
        from window_transfers
        union all
        select timestamp '{{start_date}}' as ts,
               -1 as evt_block_number,
               from_hex('0000000000000000000000000000000000000000000000000000000000000000') as evt_tx_hash,
               -1 as evt_index,
               u.user_addr,
               coalesce(ob.bal, 0) as amount_change,
               oref.ref_code
        from (
            select user_addr from window_transfers
            union
            -- > 1e-9: skip sub-dust opening balances (consistent with the
            -- original's own 1e-9 idle-fill cutoff). Negligible vs the > 0 the
            -- original technically counts; far below the 1e-6 xcheck tolerance.
            select user_addr from opening_balance where bal > 1e-9
        ) u
        left join opening_balance ob   on u.user_addr = ob.user_addr
        left join opening_ref      oref on u.user_addr = oref.user_addr
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
        from daily_start_balances s
        -- Skip the start-of-day fill ON the window's first day: the synthetic
        -- opening event (block -1, see raw_transfers) already sits at start_date
        -- 00:00 with the seeded balance. Emitting the lag-based fill here too
        -- would inject a SECOND 00:00 event whose lag is 0 (no prior in-window
        -- day); with its full-day duration it would zero out the seed day's TWA.
        where s.start_of_day_balance is not null
          and s.dt > cast('{{start_date}}' as date)
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

    -- Idle-fill is capped at the window end (end_date - 1 day). For a holder
    -- whose balance is still positive at the window end this keeps them filled to
    -- the last day of the window; for the final window (end_date >= today) the
    -- cap becomes current_date, matching the un-windowed behaviour.
    user_date_ranges as (
        select deb.user_addr,
               min(deb.dt) as first_dt,
               least(
                   case when ufb.final_balance > 1e-9 then greatest(max(deb.dt), current_date) else max(deb.dt) end,
                   -- cast (not `date '...'`) so a datetime-formatted param value
                   -- ("YYYY-MM-DD 00:00:00" from Dune's UI) is still accepted.
                   cast('{{end_date}}' as date) - interval '1' day
               ) as last_dt
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
               'base' as blockchain,
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
