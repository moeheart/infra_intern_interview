from __future__ import annotations

import math
import re
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import math

from vm_cli.errors import CliError, ProviderError
from vm_cli.fleet_store import FleetStore
from vm_cli.models import (
    CapacityRecord,
    CreateRequest,
    FleetDestroyResult,
    FleetMember,
    FleetRecord,
    FleetSummary,
    InstanceRecord,
)
from vm_cli.providers.base import VMProvider


@dataclass
class _CreateOutcome:
    candidate: CapacityRecord
    requested: int
    created: list[InstanceRecord]


class FleetManager:
    def __init__(
        self,
        providers: dict[str, VMProvider],
        store: FleetStore,
        *,
        default_ssh_key: str,
    ) -> None:
        self.providers = providers
        self.store = store
        self.default_ssh_key = default_ssh_key

    def create_fleet(self, gpu: str, count: int, name: str | None = None) -> FleetRecord:
        if count < 1:
            raise CliError("--count must be at least 1")

        fleet_name = name or f"fleet-{gpu}-{uuid.uuid4().hex[:8]}"
        self.store.ensure_name_available(fleet_name)

        record = FleetRecord(
            name=fleet_name,
            gpu=gpu,
            requested_count=count,
            status="creating",
            created_at=_utc_now(),
        )
        self.store.save_fleet(record)

        request_counters: dict[tuple[str, str], int] = {}

        try:
            capacities = self._gather_capacity(gpu)
            exact_candidates = sorted(
                [item for item in capacities if item.available is not None and item.available > 0],
                key=lambda item: (item.provider, item.region),
            )
            unknown_candidates = [item for item in capacities if item.available is None]

            remaining = count
            pending_exact = list(exact_candidates)
            while remaining > 0 and pending_exact:
                exact_allocations: list[tuple[CapacityRecord, int]] = []
                deferred_exact: list[CapacityRecord] = []
                for index, candidate in enumerate(pending_exact):
                    if remaining <= 0:
                        deferred_exact.append(candidate)
                        continue
                    candidates_left = len(pending_exact) - index
                    fair_share = max(1, math.ceil(remaining / candidates_left))
                    allocation = min(remaining, candidate.available or 0, fair_share)
                    if allocation <= 0:
                        continue
                    exact_allocations.append((candidate, allocation))
                    remaining -= allocation
                outcomes = self._run_create_round(record, exact_allocations, request_counters)
                remaining = count - record.tracked_count
                pending_exact = list(deferred_exact)
                for outcome in outcomes:
                    leftover = (outcome.candidate.available or 0) - outcome.requested
                    if leftover > 0 and len(outcome.created) == outcome.requested:
                        pending_exact.append(
                            CapacityRecord(
                                provider=outcome.candidate.provider,
                                region=outcome.candidate.region,
                                gpu=outcome.candidate.gpu,
                                available=leftover,
                                certainty=outcome.candidate.certainty,
                            )
                        )

            remaining = count - record.tracked_count
            viable_unknown = list(unknown_candidates)
            while remaining > 0 and viable_unknown:
                allocations = self._split_remaining(remaining, len(viable_unknown))
                round_allocations = [
                    (candidate, allocation)
                    for candidate, allocation in zip(viable_unknown, allocations)
                    if allocation > 0
                ]
                outcomes = self._run_create_round(record, round_allocations, request_counters)
                remaining = count - record.tracked_count

                if sum(len(outcome.created) for outcome in outcomes) == 0:
                    break

                next_viable: list[CapacityRecord] = []
                for outcome in outcomes:
                    if len(outcome.created) >= outcome.requested:
                        next_viable.append(outcome.candidate)
                viable_unknown = next_viable

            if record.tracked_count != count:
                shortfall = count - record.tracked_count
                message = (
                    f"Unable to fulfill fleet '{fleet_name}'. Requested {count} {gpu} instances; "
                    f"short by {shortfall} after creating {record.tracked_count}."
                )
                rollback_complete = self._rollback_created_fleet(record, message)
                if not rollback_complete:
                    message = f"{message} Rollback was incomplete; fleet record was kept for recovery."
                raise CliError(message)

            record.status = "active"
            record.last_error = None
            self.store.save_fleet(record)
            return record
        except ProviderError as exc:
            message = f"Fleet '{fleet_name}' failed during creation: {exc}"
            rollback_complete = self._rollback_created_fleet(record, message)
            if not rollback_complete:
                message = f"{message} Rollback was incomplete; fleet record was kept for recovery."
            raise CliError(message) from exc
        except CliError:
            raise
        except Exception:
            if record.instances:
                self._rollback_created_fleet(record, "Fleet creation aborted unexpectedly.")
            else:
                self.store.delete_fleet(record.name)
            raise

    def list_fleets(self) -> list[FleetSummary]:
        return [fleet.to_summary() for fleet in self.store.list_fleets()]

    def get_fleet_status(self, name: str) -> FleetRecord:
        record = self.store.get_fleet(name)
        refreshed_members: list[FleetMember] = []

        with ThreadPoolExecutor(max_workers=max(1, len(record.instances) or 1)) as executor:
            futures = {
                executor.submit(self._refresh_member, member): member
                for member in record.instances
            }
            for future in as_completed(futures):
                refreshed_members.append(future.result())

        refreshed_members.sort(key=lambda item: (item.provider, item.name, item.instance_id))
        record.instances = refreshed_members
        self.store.save_fleet(record)
        return record

    def destroy_fleet(self, name: str) -> FleetDestroyResult:
        record = self.store.get_fleet(name)
        deleted_count = record.tracked_count
        record.status = "destroying"
        record.last_error = None
        self.store.save_fleet(record)

        failures = self._destroy_members(record)
        if not record.instances:
            self.store.delete_fleet(name)
            return FleetDestroyResult(
                name=name,
                status="deleted",
                deleted_count=deleted_count,
                remaining_count=0,
                message=f"Fleet '{name}' deleted.",
            )

        record.status = "destroy_failed"
        record.last_error = "; ".join(failures) if failures else "Some instances could not be deleted."
        self.store.save_fleet(record)
        return FleetDestroyResult(
            name=name,
            status=record.status,
            deleted_count=deleted_count - len(record.instances),
            remaining_count=len(record.instances),
            message=record.last_error,
        )

    def _gather_capacity(self, gpu: str) -> list[CapacityRecord]:
        records: list[CapacityRecord] = []
        with ThreadPoolExecutor(max_workers=max(1, len(self.providers))) as executor:
            futures = {
                executor.submit(provider.list_capacity, gpu): name
                for name, provider in self.providers.items()
            }
            for future in as_completed(futures):
                records.extend(future.result())
        return records

    def _run_create_round(
        self,
        record: FleetRecord,
        allocations: list[tuple[CapacityRecord, int]],
        request_counters: dict[tuple[str, str], int],
    ) -> list[_CreateOutcome]:
        if not allocations:
            return []

        futures: dict[Future[list[InstanceRecord]], tuple[CapacityRecord, int]] = {}
        outcomes: list[_CreateOutcome] = []

        with ThreadPoolExecutor(max_workers=max(1, len(allocations))) as executor:
            for candidate, allocation in allocations:
                request = self._build_create_request(record, candidate, allocation, request_counters)
                provider = self.providers[candidate.provider]
                futures[executor.submit(provider.create_instances_best_effort, request)] = (candidate, allocation)

            first_error: ProviderError | None = None
            for future in as_completed(futures):
                candidate, requested = futures[future]
                try:
                    created = future.result()
                except ProviderError as exc:
                    if exc.code == "capacity":
                        created = []
                    else:
                        if first_error is None:
                            first_error = exc
                        continue

                outcomes.append(_CreateOutcome(candidate=candidate, requested=requested, created=created))
                if created:
                    record.instances.extend(_fleet_members_from_instances(created))
                    self.store.save_fleet(record)

            if first_error is not None:
                raise first_error

        return outcomes

    def _build_create_request(
        self,
        record: FleetRecord,
        candidate: CapacityRecord,
        allocation: int,
        request_counters: dict[tuple[str, str], int],
    ) -> CreateRequest:
        counter_key = (candidate.provider, candidate.region)
        request_counters[counter_key] = request_counters.get(counter_key, 0) + 1
        counter = request_counters[counter_key]
        region_token = _slug(candidate.region)
        name = f"{record.name}-{candidate.provider}-{region_token}-{counter}"
        return CreateRequest(
            provider=candidate.provider,
            gpu=record.gpu,
            count=allocation,
            name=name,
            region=candidate.region,
            ssh_key=self.default_ssh_key,
        )

    def _rollback_created_fleet(self, record: FleetRecord, message: str) -> bool:
        if not record.instances:
            self.store.delete_fleet(record.name)
            return True

        record.status = "rolling_back"
        record.last_error = message
        self.store.save_fleet(record)

        failures = self._destroy_members(record)
        if not record.instances:
            self.store.delete_fleet(record.name)
            return True

        record.status = "rollback_failed"
        if failures:
            record.last_error = f"{message} Rollback failures: {'; '.join(failures)}"
        else:
            record.last_error = message
        self.store.save_fleet(record)
        return False

    def _destroy_members(self, record: FleetRecord) -> list[str]:
        if not record.instances:
            return []

        failures: list[str] = []
        member_lookup = {
            (member.provider, member.instance_id): member
            for member in record.instances
        }

        with ThreadPoolExecutor(max_workers=max(1, len(record.instances))) as executor:
            futures = {
                executor.submit(self._destroy_member, member): member
                for member in list(record.instances)
            }
            for future in as_completed(futures):
                member = futures[future]
                try:
                    deleted = future.result()
                except ProviderError as exc:
                    failures.append(f"{member.provider}/{member.instance_id}: {exc}")
                    continue

                if deleted:
                    key = (member.provider, member.instance_id)
                    member_lookup.pop(key, None)
                    record.instances = list(member_lookup.values())
                    self.store.save_fleet(record)

        return failures

    def _refresh_member(self, member: FleetMember) -> FleetMember:
        provider = self.providers[member.provider]
        try:
            instance = provider.get_instance(member.instance_id)
        except ProviderError as exc:
            if exc.code == "not_found":
                return FleetMember(
                    provider=member.provider,
                    instance_id=member.instance_id,
                    name=member.name,
                    region=member.region,
                    state="missing",
                    billing_type=member.billing_type,
                )
            raise
        return _fleet_member_from_instance(instance)

    def _destroy_member(self, member: FleetMember) -> bool:
        provider = self.providers[member.provider]
        try:
            provider.destroy_instance(member.instance_id)
        except ProviderError as exc:
            if exc.code == "not_found":
                return True
            raise
        return True

    def _split_remaining(self, remaining: int, candidate_count: int) -> list[int]:
        if candidate_count <= 0:
            return []

        base = remaining // candidate_count
        remainder = remaining % candidate_count
        return [base + (1 if index < remainder else 0) for index in range(candidate_count)]


def _fleet_members_from_instances(instances: list[InstanceRecord]) -> list[FleetMember]:
    return [_fleet_member_from_instance(instance) for instance in instances]


def _fleet_member_from_instance(instance: InstanceRecord) -> FleetMember:
    return FleetMember(
        provider=instance.provider,
        instance_id=instance.id,
        name=instance.name,
        region=instance.region,
        state=instance.state,
        billing_type=instance.billing_type,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower() or "global"
