from __future__ import annotations

import unittest
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vm_cli.errors import CliError, ProviderError
from vm_cli.fleet import FleetManager
from vm_cli.fleet_store import FleetStore
from vm_cli.models import ActionResult, CapacityRecord, CreateRequest, InstanceRecord
from vm_cli.providers.base import VMProvider


class FakeProvider(VMProvider):
    def __init__(
        self,
        name: str,
        *,
        capacity: list[CapacityRecord] | None = None,
        create_plan: list[int | Exception] | None = None,
        destroy_failures: dict[str, ProviderError] | None = None,
        destroy_fail_all_error: ProviderError | None = None,
    ) -> None:
        self.name = name
        self.capacity = list(capacity or [])
        self.create_plan = list(create_plan or [])
        self.destroy_failures = dict(destroy_failures or {})
        self.destroy_fail_all_error = destroy_fail_all_error
        self.create_calls: list[CreateRequest] = []
        self.destroy_calls: list[str] = []
        self.instances: dict[str, InstanceRecord] = {}
        self._sequence = 0

    def list_instances(self) -> list[InstanceRecord]:
        return list(self.instances.values())

    def get_instance(self, instance_id: str) -> InstanceRecord:
        if instance_id not in self.instances:
            raise ProviderError(self.name, f"{instance_id} not found", code="not_found")
        return self.instances[instance_id]

    def create_instances(self, req: CreateRequest) -> list[InstanceRecord]:
        return self._execute_create(req)

    def list_capacity(self, gpu: str) -> list[CapacityRecord]:
        return [item for item in self.capacity if item.gpu == gpu]

    def create_instances_best_effort(self, req: CreateRequest) -> list[InstanceRecord]:
        return self._execute_create(req)

    def stop_instance(self, instance_id: str) -> ActionResult:
        raise NotImplementedError

    def start_instance(self, instance_id: str) -> ActionResult:
        raise NotImplementedError

    def destroy_instance(self, instance_id: str) -> ActionResult:
        self.destroy_calls.append(instance_id)
        if self.destroy_fail_all_error is not None:
            raise self.destroy_fail_all_error
        error = self.destroy_failures.get(instance_id)
        if error:
            raise error
        self.instances.pop(instance_id, None)
        return ActionResult(
            provider=self.name,
            action="destroy",
            instance_id=instance_id,
            state="deleted",
            message="deleted",
        )

    def _execute_create(self, req: CreateRequest) -> list[InstanceRecord]:
        self.create_calls.append(req)
        plan_item = self.create_plan.pop(0) if self.create_plan else req.count
        if isinstance(plan_item, Exception):
            raise plan_item

        created_count = int(plan_item)
        created: list[InstanceRecord] = []
        for index in range(created_count):
            self._sequence += 1
            suffix = f"-{index + 1}" if created_count > 1 else ""
            instance = InstanceRecord(
                provider=self.name,
                id=f"{self.name}-{self._sequence}",
                name=f"{req.name}{suffix}",
                gpu=req.gpu,
                provider_gpu=req.gpu,
                region=req.region or "global",
                provider_region=req.region or "global",
                state="running",
                public_ip=None,
                private_ip=None,
                reservation_id=None,
                billing_type="on_demand",
            )
            self.instances[instance.id] = instance
            created.append(instance)
        return created


class FleetManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        workspace_tmp_root = Path(__file__).resolve().parents[2] / ".tmp_test_state"
        workspace_tmp_root.mkdir(exist_ok=True)
        self.state_path = workspace_tmp_root / f"fleets-{uuid.uuid4().hex}.json"
        self.addCleanup(self._cleanup_state)
        self.store = FleetStore(self.state_path)

    def _cleanup_state(self) -> None:
        if self.state_path.exists():
            self.state_path.unlink()

    def _manager(self, providers: dict[str, VMProvider]) -> FleetManager:
        return FleetManager(providers, self.store, default_ssh_key="default-key")

    def test_exact_capacity_fulfillment_across_multiple_providers(self) -> None:
        providers = {
            "crusoe": FakeProvider(
                "crusoe",
                capacity=[CapacityRecord("crusoe", "us-west", "h100.8x", 2, "exact")],
            ),
            "lambda": FakeProvider(
                "lambda",
                capacity=[CapacityRecord("lambda", "us-east", "h100.8x", 3, "exact")],
            ),
        }

        record = self._manager(providers).create_fleet("h100.8x", 4, name="team-a")

        self.assertEqual(record.status, "active")
        self.assertEqual(record.tracked_count, 4)
        self.assertEqual(len(providers["crusoe"].create_calls), 1)
        self.assertEqual(providers["crusoe"].create_calls[0].count, 2)
        self.assertEqual(len(providers["lambda"].create_calls), 1)
        self.assertEqual(providers["lambda"].create_calls[0].count, 2)

    def test_partial_capacity_is_filled_by_other_providers(self) -> None:
        providers = {
            "crusoe": FakeProvider(
                "crusoe",
                capacity=[CapacityRecord("crusoe", "us-west", "h100.8x", 3, "exact")],
                create_plan=[1],
            ),
            "lambda": FakeProvider(
                "lambda",
                capacity=[CapacityRecord("lambda", "us-east", "h100.8x", 3, "exact")],
            ),
        }

        record = self._manager(providers).create_fleet("h100.8x", 3, name="team-b")

        self.assertEqual(record.tracked_count, 3)
        self.assertEqual(len([m for m in record.instances if m.provider == "crusoe"]), 1)
        self.assertEqual(len([m for m in record.instances if m.provider == "lambda"]), 2)

    def test_unknown_capacity_uses_multiple_rounds_until_fulfilled(self) -> None:
        providers = {
            "lambda": FakeProvider(
                "lambda",
                capacity=[CapacityRecord("lambda", "us-west", "h100.8x", None, "unknown")],
                create_plan=[1, 0],
            ),
            "nebius": FakeProvider(
                "nebius",
                capacity=[CapacityRecord("nebius", "global", "h100.8x", None, "unknown")],
                create_plan=[1, 1],
            ),
        }

        record = self._manager(providers).create_fleet("h100.8x", 3, name="team-c")

        self.assertEqual(record.tracked_count, 3)
        self.assertEqual([call.count for call in providers["lambda"].create_calls], [2])
        self.assertEqual([call.count for call in providers["nebius"].create_calls], [1, 1])

    def test_create_stops_when_no_progress_is_possible(self) -> None:
        providers = {
            "lambda": FakeProvider(
                "lambda",
                capacity=[CapacityRecord("lambda", "us-west", "h100.8x", None, "unknown")],
                create_plan=[0],
            ),
            "nebius": FakeProvider(
                "nebius",
                capacity=[CapacityRecord("nebius", "global", "h100.8x", None, "unknown")],
                create_plan=[0],
            ),
        }

        with self.assertRaises(CliError) as ctx:
            self._manager(providers).create_fleet("h100.8x", 2, name="team-d")

        self.assertIn("Unable to fulfill fleet 'team-d'", str(ctx.exception))
        self.assertEqual(self.store.list_fleets(), [])

    def test_create_rolls_back_on_non_capacity_error(self) -> None:
        providers = {
            "crusoe": FakeProvider(
                "crusoe",
                capacity=[CapacityRecord("crusoe", "us-west", "h100.8x", 1, "exact")],
                create_plan=[ProviderError("crusoe", "bad auth", code="authentication")],
            ),
            "lambda": FakeProvider(
                "lambda",
                capacity=[CapacityRecord("lambda", "us-east", "h100.8x", 1, "exact")],
                create_plan=[1],
            ),
        }

        with self.assertRaises(CliError) as ctx:
            self._manager(providers).create_fleet("h100.8x", 2, name="team-e")

        self.assertIn("failed during creation", str(ctx.exception))
        self.assertEqual(self.store.list_fleets(), [])
        self.assertEqual(len(providers["lambda"].destroy_calls), 1)

    def test_incomplete_rollback_leaves_recoverable_fleet(self) -> None:
        provider = FakeProvider(
            "lambda",
            capacity=[CapacityRecord("lambda", "us-west", "h100.8x", 1, "exact")],
            create_plan=[1],
            destroy_fail_all_error=ProviderError("lambda", "cannot delete", code="internal"),
        )
        providers = {"lambda": provider}

        manager = self._manager(providers)
        with self.assertRaises(CliError) as ctx:
            manager.create_fleet("h100.8x", 2, name="team-f")

        self.assertIn("team-f", {item.name for item in self.store.list_fleets()})
        fleet = self.store.get_fleet("team-f")
        self.assertEqual(fleet.status, "rollback_failed")
        self.assertIn("Rollback was incomplete", str(ctx.exception))

    def test_status_marks_missing_instances(self) -> None:
        provider = FakeProvider(
            "crusoe",
            capacity=[CapacityRecord("crusoe", "us-west", "h100.8x", 1, "exact")],
        )
        manager = self._manager({"crusoe": provider})
        record = manager.create_fleet("h100.8x", 1, name="team-h")
        instance_id = record.instances[0].instance_id
        provider.instances.pop(instance_id)

        refreshed = manager.get_fleet_status("team-h")

        self.assertEqual(refreshed.instances[0].state, "missing")

    def test_duplicate_fleet_name_is_rejected(self) -> None:
        provider = FakeProvider(
            "crusoe",
            capacity=[CapacityRecord("crusoe", "us-west", "h100.8x", 1, "exact")],
        )
        manager = self._manager({"crusoe": provider})
        manager.create_fleet("h100.8x", 1, name="shared-name")

        with self.assertRaises(CliError) as ctx:
            manager.create_fleet("h100.8x", 1, name="shared-name")

        self.assertIn("already exists", str(ctx.exception))

    def test_successful_destroy_removes_fleet_record(self) -> None:
        provider = FakeProvider(
            "crusoe",
            capacity=[CapacityRecord("crusoe", "us-west", "h100.8x", 1, "exact")],
        )
        manager = self._manager({"crusoe": provider})
        manager.create_fleet("h100.8x", 1, name="team-i")

        result = manager.destroy_fleet("team-i")

        self.assertEqual(result.status, "deleted")
        self.assertEqual(self.store.list_fleets(), [])

    def test_partial_destroy_failure_leaves_destroy_failed_record(self) -> None:
        provider = FakeProvider(
            "crusoe",
            capacity=[CapacityRecord("crusoe", "us-west", "h100.8x", 2, "exact")],
        )
        manager = self._manager({"crusoe": provider})
        record = manager.create_fleet("h100.8x", 2, name="team-j")
        stuck_id = record.instances[0].instance_id
        provider.destroy_failures[stuck_id] = ProviderError("crusoe", "still deleting", code="internal")

        result = manager.destroy_fleet("team-j")

        self.assertEqual(result.status, "destroy_failed")
        fleet = self.store.get_fleet("team-j")
        self.assertEqual(fleet.status, "destroy_failed")
        self.assertEqual(len(fleet.instances), 1)
