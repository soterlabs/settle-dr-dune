"""Event signatures, topic codecs, and decoders for the DR sources.

Everything here operates on the raw ``LogRow`` returned by ``hypersync`` — the
same primitive the Dune ``*_evt_*`` decoded tables expose, just un-decoded.

topic0 hashes are keccak256 of the canonical event signature (verified on-chain
against the stUSDS contract, 2026-07):

  Transfer(address,address,uint256)
  Referral(uint16,address,uint256,uint256)          referral=topic1, owner=topic2
  Swap(address,address,address,address,uint256,uint256,uint256)  PSM3; referralCode = last data word
  Staked(address,uint256) / Withdrawn(address,uint256)           SNX staking farms
"""

from __future__ import annotations

TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
# ERC4626 vaults: Referral(uint16 indexed referral, address indexed owner, uint256 assets, uint256 shares)
REFERRAL_TOPIC0 = "0xb30a03a0e2a407f18ae0e83491331dc069d1521e292feffb071e61c8f7f40636"
# SNX staking wrappers: Referral(uint16 indexed referral, address indexed user, uint256 amount)
# — 3-arg, different topic0, but same indexed layout (code=topic1, user=topic2).
REFERRAL3_TOPIC0 = "0x16902e34d01e8d5f80ce64939920a1390ff67f2546ae43ae72ac482033300968"
SWAP_TOPIC0 = "0xdba43ee9916cb156cc32a5d3406e87341e568126a46815294073ba25c9400246"
STAKED_TOPIC0 = "0x9e71bc8eea02a63969f509818f2dafb9254532904319f9dbda79b67bd34a5f3d"
WITHDRAWN_TOPIC0 = "0x7084f5476618d8e60b11ef0d7d3f06914655adb8793e28ff7f018d4c76d505d5"

ZERO_ADDR = "0x0000000000000000000000000000000000000000"


def addr_to_topic(addr: str) -> str:
    """20-byte 0x address -> 32-byte left-zero-padded topic hex, lower-case."""
    h = addr.lower().removeprefix("0x").rjust(40, "0")
    if len(h) != 40:
        raise ValueError(f"bad address {addr!r}")
    return "0x" + "0" * 24 + h


def topic_to_addr(topic: str | None) -> str:
    """32-byte topic hex -> 20-byte 0x address (low 20 bytes), lower-case."""
    if topic is None:
        raise ValueError("topic is None")
    h = topic.lower().removeprefix("0x").rjust(64, "0")
    return "0x" + h[-40:]


def _word(data: str, i: int) -> str:
    """i-th 32-byte word (0-indexed) of a 0x data blob, as 64 hex chars."""
    h = data.removeprefix("0x")
    return h[i * 64:(i + 1) * 64]


def decode_uint(hexword: str) -> int:
    return int(hexword, 16) if hexword else 0


def transfer_value(data: str) -> int:
    """ERC20 Transfer non-indexed ``value`` (single uint256 in data)."""
    return decode_uint(_word(data, 0))


def referral_code_from_topic(topic1: str | None) -> int:
    """Referral event: uint16 ``referral`` is indexed in topic1."""
    return decode_uint((topic1 or "0x0").removeprefix("0x"))


def swap_referral_code(data: str) -> int:
    """PSM3 Swap: ``referralCode`` is the last (7th, index 6) uint256 word.

    Layout of non-indexed args in data: sender(0), amountIn(1), amountOut(2),
    referralCode(3)?  NB: assetIn/assetOut/receiver are indexed (topics 1-3);
    the non-indexed args in ``data`` are (sender, amountIn, amountOut,
    referralCode) -> referralCode is data word index 3.
    """
    h = data.removeprefix("0x")
    nwords = len(h) // 64
    if nwords == 0:
        return 0
    return decode_uint(_word(data, nwords - 1))
