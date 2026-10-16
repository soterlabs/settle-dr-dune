"""Synthetic ref codes have one audited catalog and no real-code overlap."""

from pathlib import Path
import sys

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "py"))

from drhs import synthetic_codes as registry  # noqa: E402
from drhs import twa  # noqa: E402
from drhs.revenue import monthly  # noqa: E402
from drhs.sources import holder, template_ab  # noqa: E402
from run_source import SPECS  # noqa: E402


def _codes_for(mechanism):
    return {code for code, item in registry.SYNTHETIC_CODES.items()
            if item.mechanism == mechanism}


def test_registry_covers_every_production_synthetic_source():
    holders = {target.ref_code for spec in SPECS.values()
               for target in spec.targets
               if isinstance(target, holder.HolderTarget)}
    pseudo_referrals = {program.ref_code for spec in SPECS.values()
                        for program in spec.synthetic}
    reclassifications = {
        99,
        127,
        10000,
        10001,
        *monthly.SP_UNTAGGED.values(),
    }

    assert holders == _codes_for("holder")
    assert pseudo_referrals == _codes_for("pseudo_referral")
    assert reclassifications == _codes_for("reclassification")
    assert {twa.SENTINEL} == _codes_for("sentinel")


def test_payability_matches_the_runtime_exclusion_set():
    registry_non_payable = {
        code for code, item in registry.SYNTHETIC_CODES.items()
        if not item.payable
    }
    assert monthly.NON_PAYABLE_CODES == registry_non_payable


def test_real_emitted_reroutes_are_not_synthetic_codes():
    assert template_ab.REROUTED_CODES.isdisjoint(registry.SYNTHETIC_CODES)


def test_registry_codes_are_unique_and_documented():
    assert len(registry.SYNTHETIC_CODES) == len(set(registry.SYNTHETIC_CODES))
    doc = ROOT / "docs" / "synthetic-ref-codes.md"
    text = doc.read_text(encoding="utf-8")
    for code in registry.SYNTHETIC_CODES:
        assert f"| {code} |" in text

