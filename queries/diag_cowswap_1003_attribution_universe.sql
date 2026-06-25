-- =============================================================================
-- Diagnostic: CowSwap → sUSDS attribution universe for ref_code 1003
-- =============================================================================
-- Goal: understand why the strict "sUSDS Deposit inside a CowSwap tx" signal
-- collapses to 1-17 wallets/month, while the broad "CowSwap Trade buyToken=sUSDS"
-- signal yields ~322 wallets — and which (if either) matches Amatsu's scale.
--
-- Run each numbered block separately (or comment the others out).
--
-- CowSwap settlement: 0x9008d19f58aabd9ed0d60971565aa8510560ab41
-- sUSDS (Ethereum):   0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD
-- =============================================================================

-- -----------------------------------------------------------------------------
-- BLOCK 1 — wallet-count universe per month, by definition
--   A = Trade.owner where buyToken = sUSDS            (broad / "bought sUSDS")
--   B = sUSDS Deposit.owner in a CowSwap settlement tx (strict / current query)
-- -----------------------------------------------------------------------------
with
    susds_addr as (
        select 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD as addr
    ),
    cowswap_txs as (
        select distinct evt_tx_hash
        from gnosis_protocol_v2_ethereum.gpv2settlement_evt_trade
    ),
    trade_wallets as (
        select date_trunc('month', evt_block_time) as month,
               owner as user_addr
        from gnosis_protocol_v2_ethereum.gpv2settlement_evt_trade
        cross join susds_addr s
        where buyToken = s.addr
    ),
    deposit_wallets as (
        select date_trunc('month', d.evt_block_time) as month,
               d.owner as user_addr
        from sky_ethereum.susds_evt_deposit d
        join cowswap_txs t on d.evt_tx_hash = t.evt_tx_hash
    )
select 'A_trade_into_susds'       as defn, month, count(distinct user_addr) as wallets
from trade_wallets group by 1, 2
union all
select 'B_deposit_in_cowswap_tx'  as defn, month, count(distinct user_addr)
from deposit_wallets group by 1, 2
order by month, defn;

-- -----------------------------------------------------------------------------
-- BLOCK 2 — who are the Deposit owners? (are they intermediaries?)
--   If a few addresses dominate (settlement / solvers / executors), then
--   tagging Deposit.owner is wrong: the minted shares land on an intermediary,
--   not the end user, and the strict signal is both too narrow AND mis-targeted.
-- -----------------------------------------------------------------------------
-- with
--     susds_addr as (select 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD as addr),
--     cowswap_txs as (
--         select distinct evt_tx_hash
--         from gnosis_protocol_v2_ethereum.gpv2settlement_evt_trade
--     )
-- select d.owner                              as deposit_owner,
--        count(*)                             as n_deposits,
--        sum(cast(d.shares as double) / 1e18) as total_shares_minted
-- from sky_ethereum.susds_evt_deposit d
-- join cowswap_txs t on d.evt_tx_hash = t.evt_tx_hash
-- group by 1
-- order by n_deposits desc
-- limit 40;

-- -----------------------------------------------------------------------------
-- BLOCK 3 — in CowSwap+sUSDS-Deposit txs, does Deposit.owner == Trade.owner?
--   Quantifies how often the minted-share recipient is the order owner vs an
--   intermediary. High mismatch ⇒ Deposit.owner is the wrong attribution target.
-- -----------------------------------------------------------------------------
-- with
--     susds_addr as (select 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD as addr),
--     cowswap_trades as (
--         select evt_tx_hash, owner as trade_owner, buyToken
--         from gnosis_protocol_v2_ethereum.gpv2settlement_evt_trade
--     )
-- select case when d.owner = ct.trade_owner then 'owner==trade_owner'
--             else 'owner!=trade_owner' end                  as relationship,
--        count(*)                                            as n_deposits,
--        count(distinct d.owner)                             as distinct_deposit_owners
-- from sky_ethereum.susds_evt_deposit d
-- join cowswap_trades ct on d.evt_tx_hash = ct.evt_tx_hash
-- group by 1
-- order by 2 desc;
