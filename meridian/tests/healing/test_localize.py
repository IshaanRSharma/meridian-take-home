"""Which file to open, derived rather than guessed.

The chain is `column → the primitive whose fills target it → file_map → a path`.
Every link is a fact somebody wrote down: the fill is in the frozen spec the
process owner approved, the map is in `build.json` the generator wrote. Nothing
here reads a comment convention, because that fails silently the moment a file
is renamed and a patch applied to the wrong file passes the gate for the wrong
reason.

The tests that matter are the ones where the chain *breaks*. A column nothing
fills and a map that has gone stale are both real, and in both cases the honest
answer is to say so rather than to produce a plausible path.
"""

from __future__ import annotations

from meridian.domain.frozen import FrozenSpec, SpecPrimitive
from meridian.domain.primitives import CheckConfig, FieldRef, Fill
from meridian.healing.compare import Mismatch
from meridian.healing.localize import locate, locate_error, owners_from_spec
from meridian.runtime.trace import Step


def check(key: str, *fills: tuple[str, str]) -> SpecPrimitive:
    return SpecPrimitive(
        key=key,
        primitive_type="check",
        config=CheckConfig(
            fills=tuple(
                Fill(measure=measure, field=FieldRef(entity="shipment_summary", path=path))  # type: ignore[arg-type]
                for measure, path in fills
            )
        ),
    )


SPEC = FrozenSpec(
    slug="inbound_pre_alert",
    primitives={
        "coas_valid": check(
            "coas_valid",
            ("checked", "coa_total"),
            ("passed", "coa_success"),
            ("failed", "failed_coa"),
        ),
        "invoice_complete": check(
            "invoice_complete",
            ("failed", "goods_failed"),
            ("passed", "invoices_successful"),
        ),
    },
)
FILE_MAP = {
    "coas_valid": "src/checks/coas_valid.py",
    "invoice_complete": "src/checks/invoice_complete.py",
}


def test_a_column_resolves_to_the_primitive_that_fills_it():
    assert owners_from_spec(SPEC)["coa_success"] == ("coas_valid",)
    assert owners_from_spec(SPEC)["goods_failed"] == ("invoice_complete",)


def test_a_column_nothing_fills_resolves_to_nobody():
    # Not an oversight to paper over: "an eval column nothing fills" is a stop
    # condition the generator is told to report rather than invent a source for,
    # and the sweep is where it becomes visible.
    assert "invoices_mismatched_asn" not in owners_from_spec(SPEC)


def test_the_file_comes_from_the_map_and_carries_the_slug():
    found = locate(Mismatch("coa_success", 5, 3), SPEC, FILE_MAP)

    assert found.primitive_key == "coas_valid"
    assert found.file == "agents/inbound_pre_alert/src/checks/coas_valid.py"
    assert found.signature == "coas_valid :: output_diff :: coa_success"


def test_a_stale_map_names_no_file_rather_than_a_plausible_one():
    # The bundle says FILE and somebody opens it. A path assembled from a naming
    # convention is right until a rename, and then it is a patch in the wrong
    # file that still passes the gate.
    found = locate(Mismatch("coa_success", 5, 3), SPEC, {})

    assert found.primitive_key == "coas_valid"
    assert found.file is None
    assert "file_map" in found.detail["note"]


def test_a_column_nobody_fills_is_located_on_itself_and_says_so():
    found = locate(Mismatch("invoices_mismatched_asn", 14, None), SPEC, FILE_MAP)

    assert found.primitive_key is None
    assert found.signature == "invoices_mismatched_asn :: output_diff :: unfilled"


def test_two_primitives_filling_one_column_name_neither():
    # Picking one at random is how a patch lands in a file that had nothing to
    # do with the failure. Both candidates go in the detail so a human can settle
    # it in one read.
    ambiguous = SPEC.model_copy(
        update={
            "primitives": SPEC.primitives | {"other": check("other", ("passed", "coa_success"))}
        }
    )

    found = locate(Mismatch("coa_success", 5, 3), ambiguous, FILE_MAP)

    assert found.primitive_key is None
    assert found.signature == "coa_success :: output_diff :: ambiguous"
    assert found.detail["candidates"] == ["coas_valid", "other"]


def test_two_columns_from_one_primitive_are_two_signatures():
    # Fixing `coa_success` and fixing `coa_total` are not always the same bug,
    # and bucketing them together would hide one behind the other.
    signatures = {
        locate(m, SPEC, FILE_MAP).signature
        for m in (Mismatch("coa_success", 5, 3), Mismatch("coa_total", 5, 4))
    }

    assert len(signatures) == 2


def test_an_errored_case_is_located_on_the_step_that_failed():
    steps = (
        Step(seq=1, name="extract", status="ok"),
        Step(seq=2, name="coas_valid", status="failed", error="KeyError: 'batch_no'"),
    )

    found = locate_error("KeyError: 'batch_no'", steps, SPEC, FILE_MAP)

    assert found.primitive_key == "coas_valid"
    assert found.signature == "coas_valid :: assertion :: KeyError"
    assert found.file == "agents/inbound_pre_alert/src/checks/coas_valid.py"


def test_an_error_before_any_step_is_located_on_the_entry_point():
    # The sweep-of-zero case. Nothing got far enough to be named, so the fix is
    # not in a file the bundle could name — and saying `entry_point` is what
    # tells the reader to work forwards instead of opening a check.
    found = locate_error("ImportError: no module", (), SPEC, FILE_MAP)

    assert found.primitive_key is None
    assert found.signature == "entry_point :: assertion :: ImportError"


def test_an_error_after_a_step_that_is_not_a_card_names_no_primitive():
    # `extract` is a real step and not a spec primitive, so there is no file_map
    # entry and never will be. Naming it as the primitive_key would put a
    # non-existent card in `failures` and break every join off it.
    steps = (Step(seq=1, name="extract", status="failed", error="ValueError: bad page"),)

    found = locate_error("ValueError: bad page", steps, SPEC, FILE_MAP)

    assert found.signature == "extract :: assertion :: ValueError"
    assert found.primitive_key is None
