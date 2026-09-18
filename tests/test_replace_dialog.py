"""Tests for the find & replace dialog's payload (`meadow/frontend/replace_dialog.py`)."""

from __future__ import annotations

from meadow.frontend.replace_dialog import replace_parameters


def test_the_parameters_carry_what_the_worker_reads() -> None:
    """`replace_text` gets the literal text, the target and the case switch."""
    parameters = replace_parameters(
        search="Brems",
        replace="Bremsscheibe",
        attribute="name",
        case_sensitive=False,
        approval=False,
    )

    assert parameters == {
        "search": "Brems",
        "replace": "Bremsscheibe",
        "attribute": "name",
        "case_sensitive": False,
    }


def test_an_empty_attribute_means_every_replaceable_attribute() -> None:
    """The worker decides which attributes that are — the page does not guess."""
    parameters = replace_parameters(
        search="a", replace="b", attribute="", case_sensitive=True, approval=False
    )

    assert parameters["attribute"] == ""
    assert parameters["case_sensitive"] is True


def test_the_approval_flag_only_rides_along_when_it_is_switched_on() -> None:
    """The worker must not see a disabled switch as a request."""
    without = replace_parameters(
        search="a", replace="b", attribute="", case_sensitive=False, approval=False
    )
    with_approval = replace_parameters(
        search="a", replace="b", attribute="", case_sensitive=False, approval=True
    )

    assert "send_for_approval" not in without
    assert with_approval["send_for_approval"] is True
