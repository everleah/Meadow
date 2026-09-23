"""Pure decisions of the `/products` bulk-edit wizard.

The wizard in `grid.py` renders the per-operation configuration and posts the run.
*What* an operation needs — which parameters it takes, which entries count as
missing, which rows a scope or a page means — is data, not layout, and the worker
reads exactly that payload (`meadow/worker/bulk.py`). So it lives here and is
tested without a browser (`tests/test_bulk_edit.py`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: Review status of the workflow state machine (plan/detail/workflow-freigaben.md);
#: the default target of the `send_for_approval` bulk operation.
APPROVAL_STATUS = "in_review"

#: Operations that change data and therefore offer the "send for approval
#: afterwards" switch (`change_status`, `send_for_approval` and `delete` do not).
APPROVAL_MODIFIER_OPS = (
    "set_attribute",
    "clear_attribute",
    "add_category",
    "remove_category",
    "add_relation",
    "remove_relation",
    "change_parent",
)


def approval_supported(op: str) -> bool:
    """Whether the operation may be sent for approval afterwards."""
    return op in APPROVAL_MODIFIER_OPS


def row_id(row: object) -> str | None:
    """The object id of a selected grid row (data dict or AG Grid row node)."""
    if not isinstance(row, dict):
        return None
    oid = row.get("_id")
    if not oid:
        data = row.get("data")
        if isinstance(data, dict):
            oid = data.get("_id")
    return str(oid) if oid else None


def ids_from_rows(rows: Iterable[object]) -> list[str]:
    """The object ids of grid rows, skipping the rows without one."""
    return [oid for oid in (row_id(row) for row in rows) if oid]


def build_parameters(
    op: str,
    fields: Mapping[str, Any],
    picked: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """The `parameters` payload of `op` from the wizard's field values and picks.

    `fields` holds the current value per wizard field key (the widget the config
    area built for the operation) and `picked` the object chosen per picker key.
    Values the operation does not use are left out entirely, so an incomplete
    configuration can be spotted by `missing_parameters`.
    """
    parameters: dict[str, Any] = {}
    if op in ("set_attribute", "clear_attribute"):
        parameters["attribute"] = fields.get("attribute")
        if op == "set_attribute":
            parameters["value"] = fields.get("value")
    elif op == "change_status":
        parameters["status"] = fields.get("status")
    elif op == "send_for_approval":
        parameters["approval_status"] = fields.get("approval_status")
    elif op in ("add_category", "remove_category"):
        category = picked.get("category") or {}
        parameters["category"] = category.get("id")
        definition_id = fields.get("category_definition")
        if definition_id:
            parameters["definition_id"] = str(definition_id)
    elif op == "change_parent":
        parent = picked.get("parent") or {}
        parameters["parent"] = parent.get("id")
    elif op in ("add_relation", "remove_relation"):
        parameters["definition_id"] = fields.get("relation")
        target = picked.get("relation_target") or {}
        parameters["target"] = target.get("id")
    elif op in ("release_for_channel", "withdraw_from_channel"):
        parameters["channel"] = fields.get("channel")
        if op == "release_for_channel":
            release_status = fields.get("release_status")
            if release_status:
                parameters["release_status"] = str(release_status)
    if approval_supported(op) and fields.get("send_for_approval"):
        parameters["send_for_approval"] = True
    return parameters


def missing_parameters(parameters: Mapping[str, Any]) -> list[str]:
    """The parameter keys an operation cannot run without (falsy values)."""
    return [key for key, value in parameters.items() if not value]
