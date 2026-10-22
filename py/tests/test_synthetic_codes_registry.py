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
    ordinary_user = "0x0000000000000000000000000000000000000001"
    contract_user = next(iter(monthly.PSM3_CODE0_10001["base"]))
    reclassifications = {
        monthly.reclass_susds_susdc("sUSDS", twa.SENTINEL, ordinary_user, "ethereum"),
        monthly.reclass_susds_susdc("sUSDC", twa.SENTINEL, ordinary_user, "base"),
        monthly.reclass_psm3("sUSDS", twa.SENTINEL, ordinary_user, "base"),
        monthly.reclass_psm3("sUSDS", 0, ordinary_user, "base"),
        monthly.reclass_psm3("sUSDS", 0, contract_user, "base"),
        *(monthly.reclass_sp(symbol, twa.SENTINEL, ordinary_user, "ethereum")
          for symbol in monthly.SP_UNTAGGED),
    }

    assert holders == _codes_for("holder")
    assert pseudo_referrals == _codes_for("pseudo_referral")
    assert reclassifications == _codes_for("reclassification")
    assert {twa.SENTINEL} == _codes_for("sentinel")


def test_payable_view_matches_the_runtime_exclusion_set():
    registry_excluded = {
        code for code, item in registry.SYNTHETIC_CODES.items()
        if not item.included_in_payable_view
    }
    assert monthly.NON_PAYABLE_CODES == registry_excluded


def test_only_documented_dual_origin_code_overlaps_synthetic_and_emitted():
    assert template_ab.REROUTED_CODES & registry.SYNTHETIC_CODES.keys() == {1020}


def test_documentation_matches_registry_metadata():
    doc = ROOT / "docs" / "synthetic-ref-codes.md"
    documented = {}
    for line in doc.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) < 7 or not cells[1].lstrip("-").isdigit():
            continue
        documented[int(cells[1])] = (cells[2], cells[3], cells[4])

    expected = {
        code: (item.name, item.mechanism,
               "Yes" if item.included_in_payable_view else "No")
        for code, item in registry.SYNTHETIC_CODES.items()
    }
    assert documented == expected


def test_catalog_stays_outside_checkpoint_fingerprinted_runtime():
    """An audit-only catalog must not silently become a checkpoint dependency."""
    paths = [
        *(ROOT / "py" / "drhs" / "sources").glob("*.py"),
        *(ROOT / "py" / "drhs" / "revenue").glob("*.py"),
        ROOT / "py" / "drhs" / "events.py",
        ROOT / "py" / "drhs" / "twa.py",
        ROOT / "py" / "drhs" / "state_checkpoint.py",
        ROOT / "py" / "run_source.py",
        ROOT / "py" / "run_dr_chunk.py",
    ]
    for path in paths:
        assert "synthetic_codes" not in path.read_text(encoding="utf-8"), path
