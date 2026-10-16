"""Authoritative registry of ref codes created by this pipeline.

Codes absent from this registry must come from an on-chain ``Referral`` or
``Swap`` event.  Re-routed codes such as 1004, 3006, and 4011 are deliberately
absent: the pipeline changes their attributed owner, but the code itself was
emitted on-chain.

Keep mechanism-specific configuration (holder addresses, router addresses,
eligibility windows) beside the corresponding source implementation.  Those
implementations import the numeric code from here so a new synthetic code
cannot be introduced as an unexplained literal.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

Mechanism = Literal["sentinel", "reclassification", "holder", "pseudo_referral"]


@dataclass(frozen=True)
class SyntheticCode:
    code: int
    name: str
    mechanism: Mechanism
    payable: bool
    description: str


# Missing-referral sentinel and output reclassification buckets.
UNTAGGED_SENTINEL = -999999
UNTAGGED_SUSDS = 99
UNTAGGED_SUSDC = 127
UNTAGGED_SPUSDT = 130
UNTAGGED_SPUSDC = 131
UNTAGGED_SPPYUSD = 132
L2_DEFAULT_ZERO = 10000
L2_CONTRACT_ZERO = 10001

# Pseudo-referrals inferred from an allowlisted delivery or entrypoint.
COWSWAP = 1003
SKYBASE_ONEINCH = 1020
OSERO_LIFI = 3900

# Full balances or protocol positions assigned to a code by methodology.
SKYBASE_PENDLE = 1997
SKYBASE_FLAGSHIP = 1998
SKYBASE_RISK_CAPITAL = 1999
KEEL_SOLANA_BRIDGE = 4001
SPARK_AAVE = 9001


_DEFINITIONS = (
    SyntheticCode(UNTAGGED_SENTINEL, "untagged_sentinel", "sentinel", False,
                  "Internal marker for a balance with no referral."),
    SyntheticCode(UNTAGGED_SUSDS, "untagged_susds", "reclassification", False,
                  "Untagged sUSDS house bucket."),
    SyntheticCode(UNTAGGED_SUSDC, "untagged_susdc", "reclassification", False,
                  "Untagged sUSDC house bucket."),
    SyntheticCode(UNTAGGED_SPUSDT, "untagged_spusdt", "reclassification", True,
                  "Untagged balance in Spark's spUSDT product."),
    SyntheticCode(UNTAGGED_SPUSDC, "untagged_spusdc", "reclassification", True,
                  "Untagged balance in Spark's spUSDC product."),
    SyntheticCode(UNTAGGED_SPPYUSD, "untagged_sppyusd", "reclassification", True,
                  "Untagged balance in Spark's spPYUSD product."),
    SyntheticCode(L2_DEFAULT_ZERO, "l2_default_zero", "reclassification", False,
                  "L2 PSM3 code-0 balance held by an ordinary user."),
    SyntheticCode(L2_CONTRACT_ZERO, "l2_contract_zero", "reclassification", False,
                  "L2 PSM3 code-0 balance held by an allowlisted protocol contract."),
    SyntheticCode(COWSWAP, "cowswap", "pseudo_referral", True,
                  "CowSwap delivery inferred from the settlement contract."),
    SyntheticCode(SKYBASE_ONEINCH, "skybase_oneinch", "pseudo_referral", True,
                  "Skybase 1inch activity inferred from an allowlisted entrypoint."),
    SyntheticCode(OSERO_LIFI, "osero_lifi", "pseudo_referral", True,
                  "Osero frontend delivery inferred from Li.Fi integrator events."),
    SyntheticCode(SKYBASE_PENDLE, "skybase_pendle", "holder", True,
                  "Skybase Pendle SY-sUSDS backing."),
    SyntheticCode(SKYBASE_FLAGSHIP, "skybase_flagship", "holder", True,
                  "Skybase Morpho USDS Flagship position."),
    SyntheticCode(SKYBASE_RISK_CAPITAL, "skybase_risk_capital", "holder", True,
                  "Skybase Morpho USDS Risk Capital position."),
    SyntheticCode(KEEL_SOLANA_BRIDGE, "keel_solana_bridge", "holder", True,
                  "USDS held by the Solana OFT bridge."),
    SyntheticCode(SPARK_AAVE, "spark_aave", "holder", True,
                  "USDS held by Aave's aEthUSDS contract."),
)

if len({item.code for item in _DEFINITIONS}) != len(_DEFINITIONS):
    raise RuntimeError("duplicate synthetic ref code")
if len({item.name for item in _DEFINITIONS}) != len(_DEFINITIONS):
    raise RuntimeError("duplicate synthetic ref-code name")

SYNTHETIC_CODES = MappingProxyType({item.code: item for item in _DEFINITIONS})
NON_PAYABLE_CODES = frozenset(
    item.code for item in _DEFINITIONS if not item.payable
)

