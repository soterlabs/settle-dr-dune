-- =============================================================================
-- Diagnostic: monthly DR rewards for sUSDS (Ethereum) ref_code 1004 (Paraswap)
-- =============================================================================
-- Methodology mirrors the main pipeline exactly:
--   TWA:        same two-tier attribution + TWA tail as twa_susds_susdc_erc4626.sql
--   Rate:       XR non-Spark (0.4% APY 2024-2025 | 0.5% APY 2026+)
--               1004 >= 1000, so falls into NON_SPARK_REF_RATE, not Spark (100-999).
--   Conversion: sUSDS shares → USDS, inlined from conversion_susds.sql
--   Formula:    twa_shares / 365 × reward_per × conversion_rate = dr_usd/day
--
-- Attribution: ALL 1004 flows are routed through Paraswap (see
-- diag_susds_ref1004_txs.sql), so the Referral.owner is always a router, never
-- the user. We therefore:
--   * Identify the genuine end-recipient as the NET-POSITIVE sUSDS recipient
--     inside each 1004-tagged tx (routers + pass-through legs net out / excluded).
--   * Explicitly exclude the two Paraswap router addresses from the holder set
--     (tracking their transient/inventory balance was the main TWA inflation).
--   * Tag those recipients 1004 via the tx-level fallback referral, then forward
--     -fill (last-referral-wins) so a later real code on the wallet overwrites it.
--
-- Only time segments where the forward-filled ref_code = 1004 contribute.
-- sUSDS contract (Ethereum): 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD
-- Paraswap router addresses (referral_owner in tagged txs):
--   0x6a000f20005980200259b80c5102003040001068  (Augustus v6)
--   0xdef171fe48cf0115b1d80b88dc8eab59176fee57  (fee claimer / Augustus v5)
-- =============================================================================
with
    susds_addr as (
        select 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD as addr
    ),

    -- =========================================================================
    -- 1. Two-tier referral attribution (mirrors twa_susds_susdc_erc4626.sql)
    -- =========================================================================
    -- Txs that emitted a 1004 referral event (used to find routed recipients)
    txs_1004 as (
        select distinct evt_tx_hash
        from sky_ethereum.susds_evt_referral
        where referral = 1004
    ),

    -- Paraswap router / intermediary addresses. Per diag_susds_ref1004_txs.sql,
    -- EVERY 1004 flow is "routed": the Referral.owner is one of these routers,
    -- never the end-user. They must NOT be treated as 1004 holders (tracking
    -- their transient/inventory balance massively inflates the TWA), and they
    -- must be excluded from the recipient set too.
    routers as (
        select addr from (values
            (0x6a000f20005980200259b80c5102003040001068),  -- Paraswap Augustus v6
            (0xdef171fe48cf0115b1d80b88dc8eab59176fee57)   -- Paraswap Augustus v5 / fee claimer
        ) as t(addr)
    ),

    -- Net sUSDS change per (tx, addr) inside 1004-tagged txs. The genuine end
    -- recipient of the deposit is net-positive; routers and pass-through legs
    -- (which net to ~0 or negative) are excluded. This replaces the old
    -- "direct referral owners + every transfer recipient" union that pulled in
    -- the routers themselves and intermediate hops.
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

    -- Tier 1: latest Referral per (tx, wallet)
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

    -- Tier 2: fallback — any Referral in the tx (covers aggregator routing)
    ref_fallback as (
        select evt_tx_hash, referral as ref_code
        from (
            select evt_tx_hash, referral,
                   row_number() over (
                       partition by evt_tx_hash
                       order by evt_index desc
                   ) as rn
            from sky_ethereum.susds_evt_referral
        ) t
        where rn = 1
    ),

    -- =========================================================================
    -- 2. Signed sUSDS transfers for target wallets
    -- =========================================================================
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

    -- =========================================================================
    -- 3. TWA tail (identical algorithm to twa_susds_susdc_erc4626.sql)
    -- =========================================================================
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

    -- =========================================================================
    -- 4. XR rate schedule — non-Spark (1004 >= 1000, not in Spark range 100-999)
    --    reward_per = 365 × (exp(ln(1 + apy) / 365) − 1)
    -- =========================================================================
    xr_rates (start_dt, end_dt, apy, reward_per) as (
        values
            (date '2024-01-01', date '2025-12-31',
             0.004,
             365.0 * (exp(ln(1.004) / 365.0) - 1.0)),
            (date '2026-01-01', date '2030-12-31',
             0.005,
             365.0 * (exp(ln(1.005) / 365.0) - 1.0))
    ),

    -- =========================================================================
    -- 5. sUSDS conversion rate (inlined from conversion_susds.sql)
    --    assets/shares at last Deposit or Withdraw event each day, forward-filled
    -- =========================================================================
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

    -- =========================================================================
    -- 6. Daily DR: twa_shares / 365 × reward_per × conversion_rate
    -- =========================================================================
    daily_dr as (
        select
            c.user_addr,
            c.dt,
            c.twa_balance                                              as twa_shares,
            c.twa_balance * conv.susds_rate                            as twa_usds,
            c.twa_balance / 365.0 * xr.reward_per * conv.susds_rate   as dr_usd,
            xr.apy,
            xr.reward_per,
            conv.susds_rate
        from complete_daily c
        join xr_rates        xr   on c.dt between xr.start_dt and xr.end_dt
        join daily_conversion conv on c.dt = conv.dt
    )

-- =========================================================================
-- 7. Monthly totals
-- =========================================================================
select
    date_trunc('month', dt)       as month,
    1004                          as ref_code,
    'sUSDS'                       as token,
    'ethereum'                    as blockchain,
    count(distinct user_addr)     as active_wallets,
    round(avg(twa_usds),    2)    as avg_daily_twa_usds,
    round(sum(twa_usds),    2)    as sum_twa_usds,
    round(sum(dr_usd),      4)    as dr_usd,
    min(apy)                      as apy,
    min(reward_per)               as reward_per,
    avg(susds_rate)               as avg_susds_conversion_rate
from daily_dr
group by 1
order by 1
