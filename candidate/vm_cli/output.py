from __future__ import annotations

import json
from typing import Any

from vm_cli.models import ActionResult, InstanceRecord


def emit(value: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(_serialize(value), indent=2))
        return

    if isinstance(value, InstanceRecord):
        _print_table([value])
        return
    if isinstance(value, ActionResult):
        _print_table([value])
        return
    if isinstance(value, list) and value and isinstance(value[0], InstanceRecord):
        _print_table(value)
        return
    if isinstance(value, list) and value and isinstance(value[0], ActionResult):
        _print_table(value)
        return
    if isinstance(value, list) and not value:
        print("No results.")
        return
    print(json.dumps(_serialize(value), indent=2))


def _serialize(value: Any) -> Any:
    if isinstance(value, InstanceRecord):
        return value.to_dict()
    if isinstance(value, ActionResult):
        return value.to_dict()
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


def _print_table(rows: list[InstanceRecord] | list[ActionResult]) -> None:
    if not rows:
        print("No results.")
        return

    serialized = [_serialize(row) for row in rows]
    columns = [key for key in serialized[0].keys() if key != "raw"]
    widths = {
        column: max(len(column), *(len(_cell(row.get(column))) for row in serialized))
        for column in columns
    }

    header = "  ".join(column.ljust(widths[column]) for column in columns)
    divider = "  ".join("-" * widths[column] for column in columns)
    print(header)
    print(divider)
    for row in serialized:
        print("  ".join(_cell(row.get(column)).ljust(widths[column]) for column in columns))


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)
