-- =============================================================================
-- Template D: USDS Token Rewards Contracts per-user daily TWA
--             (Sky Farm, Spk Farm, Chronicle) on Ethereum
-- -----------------------------------------------------------------------------
-- These are Synthetix-style `StakingRewards` clones with a referral-emitting
-- wrapper. Unlike the ERC4626 vaults, balance is NOT tracked from share
-- Transfers — it is tracked from the staking events:
--   Staked(address indexed user, uint256 amount)      -> + amount (USDS)
--   Withdrawn(address indexed user, uint256 amount)    -> - amount (USDS)
-- ref_code comes from the wrapper's `Referral` event (referral + owner),
-- matched by (tx_hash, user) and forward-filled (last-referral-wins).
--
-- The three farms share the same event signatures; we read each event type once
-- and distinguish farms by contract_address via the token_targets join.
--
-- Output symbol distinguishes the farms (USDS-SKY / USDS-SPK / USDS-CLE) so the
-- downstream aggregator can map them (Dune uses USDS-SKY/USDS-SPK; Chronicle is
-- not in Dune at all). Untagged stays -999999 here (Amatsu maps these to 127
-- downstream — see queries/README.md).
--
-- TABLE NAMES (verified against Dune <chain>.contracts + information_schema, 2026-06):
-- All three contracts share the decoded contract "StakingRewards", so they live in
-- ONE set of tables, distinguished by contract_address:
--   sky_ethereum.stakingrewards_evt_{staked,withdrawn,referral}
-- (Confirmed events present: staked, withdrawn, referral, rewardpaid, rewardadded, …)
-- PARAMETERS:
--   {{end_date}} (date) - scan cutoff, exclusive. Start is hardcoded to 2024-09-01.
--   Lower {{end_date}} (e.g. 2025-01-01) for a cheap, window-capped run;
--   default 2030-01-01 = full history. The cutoff is applied at the leaf event
--   scan so it actually reduces bytes scanned / cost.
-- =============================================================================
with
    token_targets (blockchain, token_symbol, token_addr, decimals, start_date) as (
        values
            ('ethereum', 'USDS-SKY', 0x0650CAF159C5A49f711e8169D4336ECB9b950275, 18, date '2024-09-01'), -- Sky Farm (USDS -> SKY)
            ('ethereum', 'USDS-SPK', 0x173e314C7635B45322cd8Cb14f44b312e079F3af, 18, date '2024-09-01'), -- Spk Farm (USDS -> SPK)
            ('ethereum', 'USDS-CLE', 0x10ab606b067c9c461d8893c47c7512472e19e2ce, 18, date '2024-09-01')  -- Chronicle (USDS -> CLE points)
    ),

    -- -------------------------------------------------------------------------
    -- 1. Referral events from the staking wrappers.
    -- -------------------------------------------------------------------------
    raw_referral_events as (
        -- StakingRewards Referral event columns: referral (code), user, amount (no "owner").
        select 'ethereum' as blockchain, r.evt_block_number, r.evt_tx_hash, r.evt_index,
               r.user as user_addr, r.contract_address, r.referral as ref_code
        from sky_ethereum.stakingrewards_evt_referral r
        join token_targets tt on r.contract_address = tt.token_addr and tt.blockchain = 'ethereum'
    ),

    latest_referral_per_tx as (
        select evt_tx_hash, user_addr, contract_address, blockchain, ref_code
        from (
            select evt_tx_hash, user_addr, contract_address, blockchain, ref_code,
                   row_number() over (
                       partition by evt_tx_hash, user_addr, contract_address, blockchain
                       order by evt_index desc
                   ) as rn
            from raw_referral_events
        ) r
        where r.rn = 1
    ),

    -- -------------------------------------------------------------------------
    -- 2. Balance changes from Staked (+) / Withdrawn (-), with ref_code attached.
    -- -------------------------------------------------------------------------
    raw_transfers_with_referral as (
        -- Staked -> positive
        select 'ethereum' as blockchain, tt.token_addr as contract_address,
               e.evt_block_time as ts, e.evt_block_number, e.evt_tx_hash, e.evt_index,
               e.user as user_addr,
               cast(e.amount as double) / power(10, tt.decimals) as amount_change,
               lr.ref_code
        from sky_ethereum.stakingrewards_evt_staked e
        join token_targets tt on e.contract_address = tt.token_addr and tt.blockchain = 'ethereum'
        left join latest_referral_per_tx lr
            on e.evt_tx_hash = lr.evt_tx_hash and e.user = lr.user_addr
            and e.contract_address = lr.contract_address and lr.blockchain = 'ethereum'
        where date(e.evt_block_time) >= tt.start_date and e.evt_block_time < timestamp '{{end_date}}'
        union all
        -- Withdrawn -> negative
        select 'ethereum', tt.token_addr, e.evt_block_time, e.evt_block_number, e.evt_tx_hash, e.evt_index,
               e.user,
               -cast(e.amount as double) / power(10, tt.decimals),
               lr.ref_code
        from sky_ethereum.stakingrewards_evt_withdrawn e
        join token_targets tt on e.contract_address = tt.token_addr and tt.blockchain = 'ethereum'
        left join latest_referral_per_tx lr
            on e.evt_tx_hash = lr.evt_tx_hash and e.user = lr.user_addr
            and e.contract_address = lr.contract_address and lr.blockchain = 'ethereum'
        where date(e.evt_block_time) >= tt.start_date and e.evt_block_time < timestamp '{{end_date}}'
    ),

    -- =========================================================================
    -- SHARED TWA TAIL (identical to query_5358161). Do not diverge between files.
    -- =========================================================================
    running_balances as (
        select
            blockchain, contract_address, user_addr, date(ts) as dt, ts,
            evt_block_number, evt_tx_hash, evt_index, amount_change,
            sum(amount_change) over (
                partition by blockchain, contract_address, user_addr
                order by evt_block_number asc, evt_index asc rows unbounded preceding
            ) as running_balance,
            coalesce(
                last_value(ref_code) ignore nulls over (
                    partition by blockchain, contract_address, user_addr
                    order by evt_block_number asc, evt_index asc rows unbounded preceding
                ), -999999
            ) as current_ref_code
        from raw_transfers_with_referral
    ),

    daily_end_balances as (
        select blockchain, contract_address, user_addr, dt,
               running_balance as end_of_day_balance,
               current_ref_code as end_of_day_ref_code
        from (
            select blockchain, contract_address, user_addr, dt, running_balance, current_ref_code,
                   row_number() over (
                       partition by blockchain, contract_address, user_addr, dt
                       order by evt_block_number desc, evt_index desc
                   ) as rn
            from running_balances
        ) t
        where rn = 1
    ),

    user_days as (
        select distinct blockchain, contract_address, user_addr, dt from running_balances
    ),

    daily_start_balances as (
        select
            ud.blockchain, ud.contract_address, ud.user_addr, ud.dt,
            coalesce(
                lag(deb.end_of_day_balance, 1) over (
                    partition by ud.blockchain, ud.contract_address, ud.user_addr order by ud.dt
                ), 0
            ) as start_of_day_balance,
            lag(deb.end_of_day_ref_code, 1) over (
                partition by ud.blockchain, ud.contract_address, ud.user_addr order by ud.dt
            ) as start_of_day_ref_code
        from user_days ud
        left join daily_end_balances deb
            on ud.blockchain = deb.blockchain and ud.contract_address = deb.contract_address
            and ud.user_addr = deb.user_addr and ud.dt = deb.dt
    ),

    time_weighted_balances_with_daily_start as (
        select blockchain, contract_address, user_addr, dt, ts,
               evt_block_number, evt_tx_hash, evt_index, running_balance, current_ref_code,
               'transaction' as event_type
        from running_balances
        union all
        select s.blockchain, s.contract_address, s.user_addr, s.dt,
               cast(s.dt as timestamp) as ts,
               0 as evt_block_number,
               from_hex('0000000000000000000000000000000000000000000000000000000000000000') as evt_tx_hash,
               -1 as evt_index,
               s.start_of_day_balance as running_balance,
               coalesce(s.start_of_day_ref_code, -999999) as current_ref_code,
               'daily_start' as event_type
        from daily_start_balances s
        where s.start_of_day_balance is not null
    ),

    time_weighted_balances_with_end as (
        select blockchain, contract_address, user_addr, dt, ts,
               evt_block_number, evt_tx_hash, evt_index, running_balance, current_ref_code, event_type,
               date_diff('second', ts,
                   coalesce(
                       lead(ts, 1) over (
                           partition by blockchain, contract_address, user_addr, dt
                           order by evt_block_number asc, evt_index asc
                       ),
                       dt + interval '1' day
                   )
               ) as duration_seconds
        from time_weighted_balances_with_daily_start
    ),

    daily_referral_segments as (
        select blockchain, contract_address, user_addr, dt, current_ref_code,
               case when 86400 > 0 then sum(running_balance * duration_seconds) / 86400.0 else 0 end
                   as segment_time_weighted_balance,
               sum(duration_seconds) as segment_duration_seconds,
               sum(running_balance * duration_seconds) as segment_balance_time_product
        from time_weighted_balances_with_end
        where date(ts) = dt
        group by blockchain, contract_address, user_addr, dt, current_ref_code
    ),

    user_date_ranges as (
        select blockchain, contract_address, user_addr,
               min(dt) as first_transaction_date,
               greatest(max(dt), current_date) as last_transaction_date
        from daily_end_balances
        group by 1, 2, 3
    ),

    complete_user_dates as (
        select u.blockchain, u.contract_address, u.user_addr, d.dt
        from user_date_ranges u
        cross join unnest(sequence(u.first_transaction_date, u.last_transaction_date, interval '1' day)) as d(dt)
    ),

    complete_daily_balances as (
        select
            c.blockchain, c.contract_address, c.user_addr, c.dt,
            case when drs.user_addr is not null then 'transaction_day' else 'no_transaction_day' end as day_type,
            coalesce(
                drs.current_ref_code,
                last_value(d.end_of_day_ref_code) ignore nulls over (
                    partition by c.blockchain, c.contract_address, c.user_addr order by c.dt rows unbounded preceding
                ),
                -999999
            ) as ref_code,
            coalesce(
                drs.segment_time_weighted_balance,
                last_value(d.end_of_day_balance) ignore nulls over (
                    partition by c.blockchain, c.contract_address, c.user_addr order by c.dt rows unbounded preceding
                )
            ) as time_weighted_avg_balance,
            drs.segment_duration_seconds,
            drs.segment_balance_time_product
        from complete_user_dates c
        left join daily_referral_segments drs
            on c.blockchain = drs.blockchain and c.contract_address = drs.contract_address
            and c.user_addr = drs.user_addr and c.dt = drs.dt
        left join daily_end_balances d
            on c.blockchain = d.blockchain and c.contract_address = d.contract_address
            and c.user_addr = d.user_addr and c.dt = d.dt
    )

select
    b.blockchain,
    b.contract_address,
    tt.token_symbol as symbol,
    b.user_addr,
    b.dt,
    b.ref_code,
    b.time_weighted_avg_balance,
    b.day_type,
    b.segment_duration_seconds,
    b.segment_balance_time_product
from complete_daily_balances b
join token_targets tt
    on b.blockchain = tt.blockchain and b.contract_address = tt.token_addr
where b.time_weighted_avg_balance > 0
order by b.blockchain, b.contract_address, b.user_addr, b.dt, b.ref_code;
