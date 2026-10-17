"""The AI suggestion history: the audit projection, formatted for the dialog.

`GET /ai/history` answers with the `ai_generated` audit rows (`timestamp`,
`user_id`, `metadata`). `history_rows` turns them into table rows without a UI
context, so these tests pin the contract: the metadata is shown, a missing or
foreign entry must not break the table, and nothing beyond the metadata is read —
the prompt and the script never leave the core
(`plan/detail/ki-assistenz.md` §6.7).
"""

from __future__ import annotations

from meadow.frontend.ai_assist import history_rows


def test_a_recorded_suggestion_carries_its_metadata() -> None:
    rows = history_rows(
        [
            {
                "timestamp": "2026-10-17T09:12:44.123456Z",
                "user_id": "user:admin",
                "metadata": {
                    "area": "rule",
                    "mode": "create",
                    "target": "business_rule:sku",
                    "ok": True,
                    "model": "deepseek-chat",
                    "attempts": 1,
                    "latency_ms": 1700,
                    "tokens": 1750,
                },
            }
        ]
    )
    assert rows == [
        {
            "when": "2026-10-17 09:12",
            "mode": "create",
            "accepted": True,
            "model": "deepseek-chat",
            "ms": "1700",
            "user": "user:admin",
        }
    ]


def test_a_rejected_suggestion_is_marked_and_missing_fields_stay_empty() -> None:
    rows = history_rows(
        [
            {
                "timestamp": "2026-10-17T09:12:00Z",
                "user_id": "user:a",
                "metadata": {"mode": "edit", "ok": False},
            }
        ]
    )
    assert rows[0]["accepted"] is False
    assert rows[0]["model"] == ""
    assert rows[0]["ms"] == ""


def test_an_entry_without_metadata_does_not_break_the_table() -> None:
    rows = history_rows([{"timestamp": None, "user_id": None}, "junk", {"metadata": "nope"}])
    assert len(rows) == 2
    assert rows[0]["when"] == ""
    assert rows[0]["user"] == ""
    assert rows[0]["accepted"] is False
