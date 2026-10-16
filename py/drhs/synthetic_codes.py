"""Audited registry of every referral code created by this pipeline.

Codes absent from this registry are expected to come from an on-chain
``Referral`` or PSM3 ``Swap`` event. Re-routed codes such as 1004, 3006, and
4011 are deliberately absent: attribution changes, but those codes were
emitted on-chain.

The production mechanisms retain their local configuration so adding this
catalog does not invalidate month-end checkpoint fingerprints. Registry tests
compare this catalog with every wired holder target, pseudo-referral program,
reclassification bucket, and the TWA sentinel; drift fails CI.
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


_DEFINITIONS = (
    SyntheticCode(-999999, "untagged_sentinel", "sentinel", False,
                  "Internal marker for a balance with no referral."),
    SyntheticCode(99, "untagged_susds", "reclassification", False,
                  "Untagged sUSDS house bucket."),
    SyntheticCode(127, "untagged_susdc", "reclassification", False,
                  "Untagged sUSDC house bucket."),
    SyntheticCode(130, "untagged_spusdt", "reclassification", True,
                  "Untagged balance in Spark's spUSDT product."),
    SyntheticCode(131, "untagged_spusdc", "reclassification", True,
                  "Untagged balance in Spark's spUSDC product."),
    SyntheticCode(132, "untagged_sppyusd", "reclassification", True,
                  "Untagged balance in Spark's spPYUSD product."),
    SyntheticCode(10000, "l2_default_zero", "reclassification", False,
                  "L2 PSM3 code-0 balance held by an ordinary user."),
    SyntheticCode(10001, "l2_contract_zero", "reclassification", False,
                  "L2 PSM3 code-0 balance held by an allowlisted protocol contract."),
    SyntheticCode(1003, "cowswap", "pseudo_referral", True,
                  "CowSwap delivery inferred from the settlement contract."),
    SyntheticCode(1020, "skybase_oneinch", "pseudo_referral", True,
                  "Skybase 1inch activity inferred from an allowlisted entrypoint."),
    SyntheticCode(3900, "osero_lifi", "pseudo_referral", True,
                  "Osero frontend delivery inferred from Li.Fi integrator events."),
    SyntheticCode(1997, "skybase_pendle", "holder", True,
                  "Skybase Pendle SY-sUSDS backing."),
    SyntheticCode(1998, "skybase_flagship", "holder", True,
                  "Skybase Morpho USDS Flagship position."),
    SyntheticCode(1999, "skybase_risk_capital", "holder", True,
                  "Skybase Morpho USDS Risk Capital position."),
    SyntheticCode(4001, "keel_solana_bridge", "holder", True,
                  "USDS held by the Solana OFT bridge."),
    SyntheticCode(9001, "spark_aave", "holder", True,
                  "USDS held by Aave's aEthUSDS contract."),
)

if len({item.code for item in _DEFINITIONS}) != len(_DEFINITIONS):
    raise RuntimeError("duplicate synthetic ref code")
if len({item.name for item in _DEFINITIONS}) != len(_DEFINITIONS):
    raise RuntimeError("duplicate synthetic ref-code name")

SYNTHETIC_CODES = MappingProxyType({item.code: item for item in _DEFINITIONS})
