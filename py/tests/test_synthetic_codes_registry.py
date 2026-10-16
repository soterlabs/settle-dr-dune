"""Synthetic ref codes have one registry and cannot overlap real reroutes."""

from pathlib import Path
import sys

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "py"))

from drhs import synthetic_codes as codes  # noqa: E402
from drhs.revenue import monthly  # noqa: E402
from drhs.sources import holder, template_ab  # noqa: E402
from run_source import SPECS  # noqa: E402


def _codes_for(mechanism):
    return {code for code, item in codes.SYNTHETIC_CODES.items()
            if item.mechanism == mechanism}


def test_registry_covers_every_production_synthetic_source():
    holders = {target.ref_code for spec in SPECS.values()
               for target in spec.targets
               if isinstance(target, holder.HolderTarget)}
    pseudo_referrals = {program.ref_code for spec in SPECS.values()
                        for program in spec.synthetic}
    reclassifications = {
        codes.UNTAGGED_SUSDS,
        codes.UNTAGGED_SUSDC,
        codes.L2_DEFAULT_ZERO,
        codes.L2_CONTRACT_ZERO,
        *monthly.SP_UNTAGGED.values(),
    }

    assert holders == _codes_for("holder")
    assert pseudo_referrals == _codes_for("pseudo_referral")
    assert reclassifications == _codes_for("reclassification")
    assert _codes_for("sentinel") == {codes.UNTAGGED_SENTINEL}


def test_non_payable_view_is_derived_from_registry():
    assert monthly.NON_PAYABLE_CODES == {
        code for code, item in codes.SYNTHETIC_CODES.items()
        if not item.payable
    }


def test_real_emitted_reroutes_are_not_synthetic_codes():
    assert template_ab.REROUTED_CODES.isdisjoint(codes.SYNTHETIC_CODES)


def test_registry_codes_are_unique_and_documented():
    assert len(codes.SYNTHETIC_CODES) == len(set(codes.SYNTHETIC_CODES))
    doc = Path(__file__).resolve().parents[2] / "docs" / "synthetic-ref-codes.md"
    text = doc.read_text(encoding="utf-8")
    for code in codes.SYNTHETIC_CODES:
        assert f"| {code} |" in text

