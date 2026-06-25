-- =============================================================================
-- Diagnostic: sUSDS Ethereum transactions tagged with ref_code 4011
-- =============================================================================
-- Approach: start from Referral events (ground truth for what was tagged 4011),
-- then compute NET sUSDS change per (tx, user_addr) across all transfers in
-- that tx. Self-cancelling vault/router legs (same wallet deposits then withdraws
-- the same amount in one tx, net ≈ 0) are filtered out, leaving only the wallets
-- with a genuine balance change.
--
-- sUSDS contract (Ethereum): 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD
-- =============================================================================
with
    -- ── All Referral events for code 4011 on sUSDS ───────────────────────────
    refs_4011 as (
        select
            evt_block_time,
            evt_block_number,
            evt_tx_hash,
            evt_index,
            owner                              as referral_owner,
            cast(assets as double) / 1e18      as assets_usds,
            cast(shares as double) / 1e18      as shares_susds
        from sky_ethereum.susds_evt_referral
        where referral = 4011
    ),

    -- ── All sUSDS transfers in those txs (signed: deposit +, withdrawal −) ──
    transfers_in_tagged_txs as (
        select
            tr.evt_tx_hash,
            tr."to"                             as user_addr,
            cast(tr.value as double) / 1e18     as amount
        from sky_ethereum.susds_evt_transfer tr
        where tr.contract_address = 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD
          and tr.evt_tx_hash in (select evt_tx_hash from refs_4011)
          and tr."to" != 0x0000000000000000000000000000000000000000
          and tr."to" != tr."from"

        union all

        select
            tr.evt_tx_hash,
            tr."from",
            -cast(tr.value as double) / 1e18
        from sky_ethereum.susds_evt_transfer tr
        where tr.contract_address = 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD
          and tr.evt_tx_hash in (select evt_tx_hash from refs_4011)
          and tr."from" != 0x0000000000000000000000000000000000000000
          and tr."from" != tr."to"
    ),

    -- ── Net sUSDS change per (tx, user): filters out self-cancelling legs ────
    net_per_user as (
        select
            evt_tx_hash,
            user_addr,
            round(sum(amount), 6) as net_susds
        from transfers_in_tagged_txs
        group by evt_tx_hash, user_addr
        having abs(sum(amount)) > 0.0001
    )

-- ── Join referral event details onto net transfer rows ───────────────────────
select
    r.evt_block_time                        as block_time,
    r.evt_block_number                      as block_number,
    r.evt_tx_hash                           as tx_hash,
    r.referral_owner,
    round(r.assets_usds,  6)                as referral_assets_usds,
    round(r.shares_susds, 6)                as referral_shares_susds,
    n.user_addr                             as net_recipient,
    n.net_susds,
    case
        when n.user_addr = r.referral_owner then 'direct'
        else                                     'routed'
    end                                     as flow_type
from refs_4011 r
join net_per_user n on r.evt_tx_hash = n.evt_tx_hash
order by r.evt_block_number asc, r.evt_tx_hash, n.net_susds desc
