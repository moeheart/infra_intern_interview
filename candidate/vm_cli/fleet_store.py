from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from vm_cli.errors import CliError
from vm_cli.models import FleetMember, FleetRecord


class FleetStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def list_fleets(self) -> list[FleetRecord]:
        data = self._load_data()
        return [self._deserialize_fleet(item) for item in data.get("fleets", [])]

    def get_fleet(self, name: str) -> FleetRecord:
        for fleet in self.list_fleets():
            if fleet.name == name:
                return fleet
        raise CliError(f"Fleet '{name}' was not found.")

    def save_fleet(self, fleet: FleetRecord) -> None:
        data = self._load_data()
        fleets = [item for item in data.get("fleets", []) if item.get("name") != fleet.name]
        fleets.append(fleet.to_dict())
        data["fleets"] = sorted(fleets, key=lambda item: item["name"])
        self._write_data(data)

    def delete_fleet(self, name: str) -> None:
        data = self._load_data()
        fleets = [item for item in data.get("fleets", []) if item.get("name") != name]
        if len(fleets) == len(data.get("fleets", [])):
            raise CliError(f"Fleet '{name}' was not found.")
        data["fleets"] = fleets
        self._write_data(data)

    def ensure_name_available(self, name: str) -> None:
        try:
            self.get_fleet(name)
        except CliError:
            return
        raise CliError(f"Fleet '{name}' already exists.")

    def _load_data(self) -> dict:
        if not self.path.exists():
            return {"fleets": []}

        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise CliError(f"Fleet state file at '{self.path}' is invalid JSON.") from exc

        if not isinstance(data, dict):
            raise CliError(f"Fleet state file at '{self.path}' must contain a JSON object.")
        fleets = data.get("fleets", [])
        if not isinstance(fleets, list):
            raise CliError(f"Fleet state file at '{self.path}' has an invalid 'fleets' field.")
        return data

    def _write_data(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with tmp_path.open("x", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.write("\n")
            os.replace(tmp_path, self.path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def _deserialize_fleet(self, data: dict) -> FleetRecord:
        return FleetRecord(
            name=data["name"],
            gpu=data["gpu"],
            requested_count=int(data["requested_count"]),
            status=data["status"],
            created_at=data["created_at"],
            instances=[
                FleetMember(
                    provider=item["provider"],
                    instance_id=item["instance_id"],
                    name=item["name"],
                    region=item["region"],
                    state=item["state"],
                    billing_type=item.get("billing_type"),
                )
                for item in data.get("instances", [])
            ],
            last_error=data.get("last_error"),
        )
