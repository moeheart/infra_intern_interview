from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vm_cli.cli import main
from vm_cli.models import FleetDestroyResult, FleetMember, FleetRecord, FleetSummary


class FakeFleetManager:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def create_fleet(self, gpu: str, count: int, name: str | None = None) -> FleetRecord:
        self.calls.append(("create", gpu, count, name))
        return FleetRecord(
            name=name or "generated",
            gpu=gpu,
            requested_count=count,
            status="active",
            created_at="2026-04-16T00:00:00Z",
            instances=[],
        )

    def list_fleets(self) -> list[FleetSummary]:
        self.calls.append(("list",))
        return [
            FleetSummary(
                name="alpha",
                gpu="h100.8x",
                requested_count=3,
                tracked_count=3,
                status="active",
                created_at="2026-04-16T00:00:00Z",
            )
        ]

    def get_fleet_status(self, name: str) -> FleetRecord:
        self.calls.append(("status", name))
        return FleetRecord(
            name=name,
            gpu="h100.8x",
            requested_count=2,
            status="active",
            created_at="2026-04-16T00:00:00Z",
            instances=[
                FleetMember(
                    provider="crusoe",
                    instance_id="vm-1",
                    name="node-1",
                    region="us-west",
                    state="running",
                    billing_type="on_demand",
                )
            ],
        )

    def destroy_fleet(self, name: str) -> FleetDestroyResult:
        self.calls.append(("destroy", name))
        return FleetDestroyResult(
            name=name,
            status="deleted",
            deleted_count=2,
            remaining_count=0,
            message="ok",
        )


class FleetCliTest(unittest.TestCase):
    def test_fleet_create_command_dispatches_to_manager(self) -> None:
        manager = FakeFleetManager()
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch("vm_cli.cli.build_providers", return_value={}):
            with patch("vm_cli.cli.build_fleet_manager", return_value=manager):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = main(["fleet", "create", "--gpu", "h100.8x", "--count", "2", "--name", "demo"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(manager.calls, [("create", "h100.8x", 2, "demo")])
        self.assertIn("name: demo", stdout.getvalue())

    def test_fleet_list_json_output_is_serialized(self) -> None:
        manager = FakeFleetManager()
        stdout = io.StringIO()

        with patch("vm_cli.cli.build_providers", return_value={}):
            with patch("vm_cli.cli.build_fleet_manager", return_value=manager):
                with redirect_stdout(stdout):
                    exit_code = main(["fleet", "list", "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload[0]["name"], "alpha")
        self.assertEqual(payload[0]["tracked_count"], 3)

    def test_fleet_status_and_destroy_commands_are_supported(self) -> None:
        manager = FakeFleetManager()
        stdout = io.StringIO()

        with patch("vm_cli.cli.build_providers", return_value={}):
            with patch("vm_cli.cli.build_fleet_manager", return_value=manager):
                with redirect_stdout(stdout):
                    exit_code = main(["fleet", "status", "alpha", "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["name"], "alpha")
        self.assertEqual(payload["instances"][0]["instance_id"], "vm-1")

        stdout = io.StringIO()
        with patch("vm_cli.cli.build_providers", return_value={}):
            with patch("vm_cli.cli.build_fleet_manager", return_value=manager):
                with redirect_stdout(stdout):
                    exit_code = main(["fleet", "destroy", "alpha", "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "deleted")
