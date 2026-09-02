"""Li.Fi integrator-anchored deliveries — the Osero frontend (``oserofrontend``).

Osero's frontend routes swaps and bridges through Li.Fi. Li.Fi never emits a
Sky ``Referral`` for the end user (when its sUSDS adapter deposits, the
Referral it fires is Li.Fi's own code, owned by the adapter — an
intermediary), so the user's sUSDS lands as a plain ``Transfer`` out of a
Li.Fi contract, exactly the CowSwap shape (docs/adding-an-aggregator.md).
The difference: Li.Fi is a *multi-tenant* router. Only deliveries whose Li.Fi
tx carries Osero's integrator id may be tagged, so the program is anchored to
Li.Fi's own events instead of to every delivery from the router:

  * same-chain swaps — the LiFiDiamond on the target chain emits
    ``LiFiGenericSwapCompleted(transactionId, integrator, referrer, receiver,
    fromAssetId, toAssetId, fromAmount, toAmount)``: the delivery tx is that
    event's tx;
  * cross-chain — the ORIGIN chain's LiFiDiamond emits ``LiFiTransferStarted
    (BridgeData)`` with the integrator and a ``transactionId``; on the
    destination chain the Li.Fi Executor swaps into the token and delivers it,
    emitting ``AssetSwapped`` / ``LiFiTransferCompleted`` with the SAME
    ``transactionId``. The destination tx is the delivery tx; the integrator
    is only known from the origin event, so both chains are scanned and joined
    on ``transactionId``.

The anchored tx set is handed to ``template_ab.synthetic_referrals`` as the
``txs`` restriction of an ordinary ``SyntheticProgram`` (delivery contracts =
Diamond + Executor); the net-positive-recipient rule, the eligibility window,
precedence (real Referral > re-routed code > pseudo-tag) and the last-wins
termination are all unchanged. topic0 hashes verified against live logs
(base 0xfadc262b…, ethereum 0x1edf4214…, 2026-07-30).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

from .. import hypersync
from ..events import topic_to_addr

_LOG = logging.getLogger(__name__)

# LiFiDiamond — CREATE3-deployed, same address on every chain Li.Fi serves.
LIFI_DIAMOND = "0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae"
# Li.Fi Executor (destination-side swap + delivery) — likewise one address.
LIFI_EXECUTOR = "0xd9b2da9c45b118e4e93a004fb1452bcdb6cc0e88"

# LiFiGenericSwapCompleted(bytes32 indexed transactionId, string integrator,
#   string referrer, address receiver, address fromAssetId, address toAssetId,
#   uint256 fromAmount, uint256 toAmount)
GENERIC_SWAP_COMPLETED_TOPIC0 = "0x38eee76fd911eabac79da7af16053e809be0e12c8637f156e77e1af309b99537"
# LiFiTransferStarted((bytes32 transactionId, string bridge, string integrator,
#   address referrer, address sendingAssetId, address receiver, uint256 minAmount,
#   uint256 destinationChainId, bool hasSourceSwaps, bool hasDestinationCall))
TRANSFER_STARTED_TOPIC0 = "0xcba69f43792f9f399347222505213b55af8e0b0b54b893085c2e27ecbe1644f1"
# LiFiTransferCompleted(bytes32 indexed transactionId, address receivingAssetId,
#   address receiver, uint256 amount, uint256 timestamp)
TRANSFER_COMPLETED_TOPIC0 = "0xb8c86983f929c6b770461983d1bbde1870408120f07123e9c12d49f35a0b4c4b"
# AssetSwapped(bytes32 transactionId, address dex, address fromAssetId,
#   address toAssetId, uint256 fromAmount, uint256 toAmount, uint256 timestamp)
ASSET_SWAPPED_TOPIC0 = "0x7bfdfdb5e3a3776976e53cb0607060f54c5312701c8cba1155cc4d5394440b38"

# EVM chain ids for the chains the pipeline scans (BridgeData.destinationChainId).
CHAIN_ID: dict[str, int] = {
    "ethereum": 1, "optimism": 10, "base": 8453, "arbitrum": 42161,
    "unichain": 130, "avalanche_c": 43114,
}


# --- ABI decoding -------------------------------------------------------------
def _w(h: str, i: int) -> str:
    return h[i * 64:(i + 1) * 64]


def _abi_string(h: str, base: int, head: int) -> str:
    """Dynamic ``string`` whose offset sits at head word ``head``; the offset
    is relative to word ``base`` (0 for a flat arg list, the tuple's first
    word for a tuple)."""
    p = base + int(_w(h, head), 16) // 32
    n = int(_w(h, p), 16)
    return bytes.fromhex(h[(p + 1) * 64:(p + 1) * 64 + 2 * n]).decode("utf-8", "replace")


@dataclass(frozen=True)
class GenericSwap:
    transaction_id: str
    integrator: str
    receiver: str
    from_asset: str
    to_asset: str
    to_amount: int


@dataclass(frozen=True)
class TransferStarted:
    transaction_id: str
    bridge: str
    integrator: str
    receiver: str
    sending_asset: str
    destination_chain_id: int
    has_destination_call: bool


def decode_generic_swap(r: hypersync.LogRow) -> GenericSwap:
    h = r.data.removeprefix("0x")
    return GenericSwap(
        transaction_id=(r.topic1 or "").lower(),
        integrator=_abi_string(h, 0, 0),
        receiver=topic_to_addr(_w(h, 2)),
        from_asset=topic_to_addr(_w(h, 3)),
        to_asset=topic_to_addr(_w(h, 4)),
        to_amount=int(_w(h, 6), 16),
    )


def decode_transfer_started(r: hypersync.LogRow) -> TransferStarted:
    h = r.data.removeprefix("0x")
    b = int(_w(h, 0), 16) // 32          # the tuple is one dynamic arg: word0 = its offset
    return TransferStarted(
        transaction_id="0x" + _w(h, b),
        bridge=_abi_string(h, b, b + 1),
        integrator=_abi_string(h, b, b + 2),
        receiver=topic_to_addr(_w(h, b + 5)),
        sending_asset=topic_to_addr(_w(h, b + 4)),
        destination_chain_id=int(_w(h, b + 7), 16),
        has_destination_call=bool(int(_w(h, b + 9), 16)),
    )


def asset_swapped_transaction_id(r: hypersync.LogRow) -> str:
    return "0x" + _w(r.data.removeprefix("0x"), 0)


# --- The program ---------------------------------------------------------------
@dataclass(frozen=True)
class IntegratorProgram:
    """A Li.Fi integrator id -> synthetic ref code. Resolves per target into an
    ordinary ``SyntheticProgram`` whose ``txs`` are the integrator's Li.Fi txs on
    the target chain (same-chain swaps + cross-chain destinations)."""
    name: str
    ref_code: int
    integrator: str                   # Li.Fi integrator id, matched case-insensitively
    origin_chains: tuple[str, ...]    # chains scanned for LiFiTransferStarted into the target
    start: date | None = None         # eligibility window, [start, end)
    end: date | None = None

    def matches(self, integrator: str) -> bool:
        return integrator.lower() == self.integrator.lower()

    def resolve(self, target, from_block: int, to_block: int, end_ts: int):
        return resolve(self, target, from_block, to_block, end_ts)


# Osero's frontend routes swaps/bridges through Li.Fi under integrator id
# ``oserofrontend``. Code 3900 is the placeholder ops assigned for the program
# (2026-09-02). PROVISIONAL start: Osero's first on-chain activity is
# 2026-04-15; ops to confirm the eligibility window. Origin chains = every
# chain the pipeline can scan; bridges from chains outside this list (polygon,
# bsc, ...) are a documented gap until a HyperSync host is added for them.
OSERO_FRONTEND = IntegratorProgram(
    "lifi_oserofrontend", 3900, "oserofrontend",
    origin_chains=("ethereum", "base", "arbitrum", "optimism", "unichain", "avalanche_c"),
    start=date(2026, 4, 1),
)


# --- Anchoring (pure) ------------------------------------------------------------
def anchored_deliveries(
    program: IntegratorProgram, target_chain: str,
    generic_rows, started_rows, completed_rows, swapped_rows,
) -> tuple[frozenset[str], frozenset[str]]:
    """-> (tx hashes on ``target_chain`` that are this integrator's Li.Fi txs,
    Li.Fi contracts that emitted in them = the delivery-contract set).

    ``generic_rows``: LiFiGenericSwapCompleted on the target chain;
    ``started_rows``: LiFiTransferStarted on the ORIGIN chains (any chain);
    ``completed_rows`` / ``swapped_rows``: LiFiTransferCompleted / AssetSwapped
    on the target chain. Cross-chain join is on ``transactionId``.
    """
    txs: set[str] = set()
    contracts: set[str] = {LIFI_DIAMOND, LIFI_EXECUTOR}
    for r in generic_rows:
        if r.transaction_hash and program.matches(decode_generic_swap(r).integrator):
            txs.add(r.transaction_hash)
            contracts.add(r.address)
    want = CHAIN_ID[target_chain]
    ids: set[str] = set()
    for r in started_rows:
        d = decode_transfer_started(r)
        if program.matches(d.integrator) and d.destination_chain_id == want:
            ids.add(d.transaction_id)
    if ids:
        for r in completed_rows:
            if r.transaction_hash and (r.topic1 or "").lower() in ids:
                txs.add(r.transaction_hash)
                contracts.add(r.address)
        for r in swapped_rows:
            if r.transaction_hash and asset_swapped_transaction_id(r) in ids:
                txs.add(r.transaction_hash)
                contracts.add(r.address)
    return frozenset(txs), frozenset(contracts)


# --- Fetch ------------------------------------------------------------------------
# The Diamond is high-volume (1.2M events / 6 months on ethereum) and HyperSync
# cannot filter on the integrator string (non-indexed), so the scan is chunked
# and each chunk is decoded and dropped, keeping only matching rows — a full
# window never sits in memory. Persistence is the pipeline's log cache
# (``hypersync.query_logs`` → drhs.logcache): the first run pays the download,
# later runs replay the Diamond stream from parquet.
_SCAN_STEP = 300_000


def _scan(chain: str, selections: list[dict], from_block: int, to_block: int,
          keep, tag: str) -> list[hypersync.LogRow]:
    kept: list[hypersync.LogRow] = []
    for lo in range(from_block, to_block + 1, _SCAN_STEP):
        hi = min(lo + _SCAN_STEP - 1, to_block)
        kept.extend(r for r in hypersync.query_logs(chain, selections, lo, hi).rows if keep(r))
    return kept


def _blocks_for(chain: str, start_ts: int, end_ts: int) -> tuple[int, int]:
    try:
        fb = hypersync.find_block_at_or_before(chain, start_ts)
    except hypersync.HyperSyncError:
        fb = 0   # start precedes the chain's genesis
    return fb, hypersync.find_block_at_or_before(chain, end_ts - 1)


def resolve(program: IntegratorProgram, target, from_block: int, to_block: int, end_ts: int):
    """Scan Li.Fi events for ``program`` and return the concrete SyntheticProgram
    for ``target`` (tx-anchored). The scan is bounded below by the program's
    eligibility start — nothing before it can be tagged anyway."""
    from .template_ab import SyntheticProgram   # local: template_ab is import-neutral

    chain = target.blockchain
    start_ts = int(datetime(target.start_date.year, target.start_date.month,
                            target.start_date.day, tzinfo=timezone.utc).timestamp())
    if program.start is not None:
        start_ts = max(start_ts, int(datetime(program.start.year, program.start.month,
                                              program.start.day, tzinfo=timezone.utc).timestamp()))
        from_block = max(from_block, hypersync.find_block_at_or_before(chain, start_ts))

    def _gen(r):
        try:
            return program.matches(decode_generic_swap(r).integrator)
        except Exception:  # noqa: BLE001 — a foreign/malformed event, never ours
            return False

    def _started(r):
        try:
            d = decode_transfer_started(r)
        except Exception:  # noqa: BLE001
            return False
        return program.matches(d.integrator) and d.destination_chain_id == CHAIN_ID[chain]

    generic = _scan(chain, [{"address": [LIFI_DIAMOND], "topics": [[GENERIC_SWAP_COMPLETED_TOPIC0]]}],
                    from_block, to_block, _gen, f"generic:{program.integrator.lower()}")
    started: list[hypersync.LogRow] = []
    for oc in program.origin_chains:
        if oc == chain or oc not in hypersync.HYPERSYNC_HOSTS:
            continue
        ofb, otb = _blocks_for(oc, start_ts, end_ts)
        started += _scan(oc, [{"address": [LIFI_DIAMOND], "topics": [[TRANSFER_STARTED_TOPIC0]]}],
                         ofb, otb, _started, f"started:{program.integrator.lower()}->{chain}")
    ids = {decode_transfer_started(r).transaction_id for r in started}
    completed: list[hypersync.LogRow] = []
    swapped: list[hypersync.LogRow] = []
    if ids:
        # transactionId is indexed on LiFiTransferCompleted → server-side filter,
        # any emitter (Executor / Receiver variants). AssetSwapped carries it in
        # data → chunked scan of the Executor, kept if the id matches.
        completed = hypersync.query_logs(
            chain, [{"topics": [[TRANSFER_COMPLETED_TOPIC0], sorted(ids)]}], from_block, to_block).rows
        swapped = _scan(chain, [{"address": [LIFI_EXECUTOR], "topics": [[ASSET_SWAPPED_TOPIC0]]}],
                        from_block, to_block,
                        lambda r: asset_swapped_transaction_id(r) in ids,
                        f"swapped:{program.integrator.lower()}->{chain}")
    txs, contracts = anchored_deliveries(program, chain, generic, started, completed, swapped)
    _LOG.info("lifi[%s] %s: %d same-chain + %d cross-chain anchors -> %d txs, %d delivery contracts",
              program.name, chain, len(generic), len(ids), len(txs), len(contracts))
    return SyntheticProgram(program.name, program.ref_code, contracts,
                            start=program.start, end=program.end, txs=txs)
