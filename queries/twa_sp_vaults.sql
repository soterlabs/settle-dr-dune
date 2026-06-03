-- =============================================================================
-- Template E: Spark vault per-user daily TWA (spUSDC, spUSDT, spPYUSD, spETH)
-- -----------------------------------------------------------------------------
-- Spark savings vaults are ERC4626 with a separate `Referral` event, identical
-- in structure to Template A: balance from ERC20 `Transfer`, ref_code from the
-- `Referral` event matched by (tx_hash, owner), forward-filled.
--
-- This query emits the FULL per-user vault balance. The deployment ratio
-- (Amatsu flat 0.9; Spark per-day TWA via query_6398769) and spETH's zero-reward
-- treatment are DOWNSTREAM (Layer 2/3) concerns and are NOT applied here.
--
-- Output schema matches result_spark_sp_usdc_sp_usdt_sp_eth_time_weighted_average_balance.
--
-- TABLE NAMES (verified against Dune <chain>.contracts + information_schema, 2026-06):
-- The sp* vaults share the decoded contract "SparkVault" -> one set of tables per
-- chain, distinguished by contract_address:
--   spark_protocol_ethereum.sparkvault_evt_{referral,transfer,deposit,withdraw}
--   spark_protocol_avalanche_c.sparkvault_evt_*  (per query_5357785)
-- Balances use canonical erc20_<chain>.evt_Transfer.
-- DECIMALS confirmed via tokens.erc20: spUSDC/spUSDT/spPYUSD = 6, spETH = 18.
-- spETH address confirmed = 0xfE6eb3b609a7C8352A241f7F3A21CEA4e9209B8f (query_5357785).
-- PARAMETERS:
--   {{end_date}} (date) - scan cutoff, exclusive. Start is hardcoded to 2024-09-01.
--   Lower {{end_date}} (e.g. 2025-01-01) for a cheap, window-capped run;
--   default 2030-01-01 = full history. The cutoff is applied at the leaf event
--   scan so it actually reduces bytes scanned / cost.
-- =============================================================================
with
    token_targets (blockchain, token_symbol, token_addr, decimals, start_date) as (
        values
            ('ethereum',    'spUSDC',  0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d, 6,  date '2024-09-01'),
            ('avalanche_c', 'spUSDC',  0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d, 6,  date '2024-09-01'),
            ('ethereum',    'spUSDT',  0xe2e7a17dFf93280dec073C995595155283e3C372, 6,  date '2024-09-01'),
            ('ethereum',    'spPYUSD', 0x80128DbB9f07b93DDE62A6daeadb69ED14a7D354, 6,  date '2024-09-01'),
            -- spETH: tracked but earns zero rewards (zeroed downstream, kept here for completeness).
            ('ethereum',    'spETH',   0xfE6eb3b609a7C8352A241f7F3A21CEA4e9209B8f, 18, date '2024-09-01')
    ),

    -- -------------------------------------------------------------------------
    -- 1. Referral events from the vaults (contract "SparkVault").
    -- -------------------------------------------------------------------------
    raw_referral_events as (
        -- ethereum vaults (spUSDC, spUSDT, spPYUSD, spETH share bytecode -> one table, filter by address)
        select 'ethereum' as blockchain, r.evt_block_number, r.evt_tx_hash, r.evt_index,
               r.owner as user_addr, r.contract_address, r.referral as ref_code
        from spark_protocol_ethereum.sparkvault_evt_referral r
        join token_targets tt on r.contract_address = tt.token_addr and tt.blockchain = 'ethereum'
        union all
        -- avalanche_c spUSDC
        select 'avalanche_c', r.evt_block_number, r.evt_tx_hash, r.evt_index,
               r.owner, r.contract_address, r.referral
        from spark_protocol_avalanche_c.sparkvault_evt_referral r
        join token_targets tt on r.contract_address = tt.token_addr and tt.blockchain = 'avalanche_c'
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
    -- 2. Vault-share Transfers (balance) via canonical erc20 spell tables.
    -- -------------------------------------------------------------------------
    raw_transfers_with_referral as (
        -- ethereum -- incoming
        select 'ethereum' as blockchain, tt.token_addr as contract_address,
               tr.evt_block_time as ts, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."to" as user_addr,
               cast(tr.value as double) / power(10, tt.decimals) as amount_change,
               lr.ref_code
        from erc20_ethereum.evt_Transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'ethereum'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."to" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'ethereum'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."to" != 0x0000000000000000000000000000000000000000
        union all
        -- ethereum -- outgoing
        select 'ethereum', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."from", -cast(tr.value as double) / power(10, tt.decimals), lr.ref_code
        from erc20_ethereum.evt_Transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'ethereum'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."from" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'ethereum'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."from" != 0x0000000000000000000000000000000000000000

        union all
        -- avalanche_c -- incoming
        select 'avalanche_c', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."to", cast(tr.value as double) / power(10, tt.decimals), lr.ref_code
        from erc20_avalanche_c.evt_Transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'avalanche_c'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."to" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'avalanche_c'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."to" != 0x0000000000000000000000000000000000000000
        union all
        -- avalanche_c -- outgoing
        select 'avalanche_c', tt.token_addr, tr.evt_block_time, tr.evt_block_number, tr.evt_tx_hash, tr.evt_index,
               tr."from", -cast(tr.value as double) / power(10, tt.decimals), lr.ref_code
        from erc20_avalanche_c.evt_Transfer tr
        join token_targets tt on tr.contract_address = tt.token_addr and tt.blockchain = 'avalanche_c'
        left join latest_referral_per_tx lr
            on tr.evt_tx_hash = lr.evt_tx_hash and tr."from" = lr.user_addr
            and tr.contract_address = lr.contract_address and lr.blockchain = 'avalanche_c'
        where date(tr.evt_block_time) >= tt.start_date and tr.evt_block_time < timestamp '{{end_date}}' and tr."from" != 0x0000000000000000000000000000000000000000
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

    user_final_balance as (
        select blockchain, contract_address, user_addr, end_of_day_balance as final_balance
        from (
            select blockchain, contract_address, user_addr, dt, end_of_day_balance,
                   row_number() over (
                       partition by blockchain, contract_address, user_addr
                       order by dt desc
                   ) as rn
            from daily_end_balances
        ) t
        where rn = 1
    ),

    user_date_ranges as (
        -- COST/PERF: only extend the no-transaction-day fill to current_date for
        -- users who STILL hold a balance. Users whose final balance is ~0 are
        -- trimmed at their last transaction day (they accrue nothing afterward),
        -- avoiding hundreds of zero-balance idle rows per exited user across full
        -- history (the dominant cost / 30-min-timeout cause).
        select deb.blockchain, deb.contract_address, deb.user_addr,
               min(deb.dt) as first_transaction_date,
               case when ufb.final_balance > 1e-9
                    then greatest(max(deb.dt), current_date)
                    else max(deb.dt) end as last_transaction_date
        from daily_end_balances deb
        join user_final_balance ufb
            on deb.blockchain = ufb.blockchain
            and deb.contract_address = ufb.contract_address
            and deb.user_addr = ufb.user_addr
        group by deb.blockchain, deb.contract_address, deb.user_addr, ufb.final_balance
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
