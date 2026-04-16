from __future__ import annotations

import sys
import time
from dataclasses import dataclass, replace
from importlib import import_module
from pathlib import Path
from typing import Any

from vm_cli.config import NebiusConfig
from vm_cli.errors import ProviderError
from vm_cli.models import ActionResult, CapacityRecord, CreateRequest, InstanceRecord
from vm_cli.providers.base import VMProvider

GPU_MAP = {
    "h100.1x": ("gpu-h100-sxm", "1gpu-16vcpu-200gb"),
    "h100.8x": ("gpu-h100-sxm", "8gpu-160vcpu-1600gb"),
    "h200.1x": ("gpu-h200-sxm", "1gpu-20vcpu-256gb"),
    "h200.8x": ("gpu-h200-sxm", "8gpu-160vcpu-2048gb"),
}
REVERSE_GPU_MAP = {value: key for key, value in GPU_MAP.items()}
STATE_MAP = {
    "CREATING": "creating",
    "RUNNING": "running",
    "STOPPING": "stopping",
    "STOPPED": "stopped",
    "STARTING": "starting",
    "DELETING": "deleting",
    "ERROR": "error",
    "STATE_UNSPECIFIED": "unknown",
}


@dataclass
class _NebiusModules:
    grpc: Any
    pb2: Any
    service_pb2: Any
    service_pb2_grpc: Any
    message_to_dict: Any


class NebiusProvider(VMProvider):
    name = "nebius"

    def __init__(self, config: NebiusConfig) -> None:
        self.config = config
        self._channel = None
        self._instance_stub = None
        self._modules: _NebiusModules | None = None

    def list_instances(self) -> list[InstanceRecord]:
        modules = self._load_modules()
        response = self._rpc(
            self._get_instance_stub().List,
            modules.service_pb2.ListInstancesRequest(parent_id=self.config.parent_id),
        )
        return [self._normalize_instance(item) for item in response.instances]

    def get_instance(self, instance_id: str) -> InstanceRecord:
        modules = self._load_modules()
        instance = self._rpc(
            self._get_instance_stub().Get,
            modules.service_pb2.GetInstanceRequest(id=instance_id),
        )
        return self._normalize_instance(instance)

    def create_instances(self, req: CreateRequest) -> list[InstanceRecord]:
        modules = self._load_modules()
        platform, preset = self._map_gpu(req.gpu)
        records: list[InstanceRecord] = []
        base_name = req.name or f"{self.name}-{req.gpu}"

        for index in range(req.count):
            name = base_name if req.count == 1 else f"{base_name}-{index + 1}"
            reservation_policy = self._build_reservation_policy(req.reservation_id)
            request = modules.service_pb2.CreateInstanceRequest(
                metadata=modules.pb2.ResourceMetadata(
                    parent_id=self.config.parent_id,
                    name=name,
                ),
                spec=modules.pb2.InstanceSpec(
                    resources=modules.pb2.ResourcesSpec(platform=platform, preset=preset),
                    reservation_policy=reservation_policy,
                ),
            )
            operation = self._rpc(self._get_instance_stub().Create, request)
            records.append(self._wait_for_instance_state(operation.resource_id, {"running"}))

        return records

    def list_capacity(self, gpu: str) -> list[CapacityRecord]:
        if gpu not in GPU_MAP:
            return []
        return [
            CapacityRecord(
                provider=self.name,
                region="global",
                gpu=gpu,
                available=None,
                certainty="unknown",
            )
        ]

    def create_instances_best_effort(self, req: CreateRequest) -> list[InstanceRecord]:
        created: list[InstanceRecord] = []
        for index in range(req.count):
            single_req = replace(
                req,
                count=1,
                name=req.name if req.count == 1 else f"{req.name}-{index + 1}",
            )
            try:
                created.extend(self.create_instances(single_req))
            except ProviderError as exc:
                if exc.code == "capacity":
                    break
                raise
        return created

    def stop_instance(self, instance_id: str) -> ActionResult:
        operation = self._rpc(
            self._get_instance_stub().Stop,
            self._load_modules().service_pb2.StopInstanceRequest(id=instance_id),
        )
        instance = self._wait_for_instance_state(operation.resource_id, {"stopped"})
        return ActionResult(
            provider=self.name,
            action="stop",
            instance_id=instance_id,
            state=instance.state,
            message="Instance stop completed.",
        )

    def start_instance(self, instance_id: str) -> ActionResult:
        operation = self._rpc(
            self._get_instance_stub().Start,
            self._load_modules().service_pb2.StartInstanceRequest(id=instance_id),
        )
        instance = self._wait_for_instance_state(operation.resource_id, {"running"})
        return ActionResult(
            provider=self.name,
            action="start",
            instance_id=instance_id,
            state=instance.state,
            message="Instance start completed.",
        )

    def destroy_instance(self, instance_id: str) -> ActionResult:
        operation = self._rpc(
            self._get_instance_stub().Delete,
            self._load_modules().service_pb2.DeleteInstanceRequest(id=instance_id),
        )
        self._wait_until_deleted(operation.resource_id)
        return ActionResult(
            provider=self.name,
            action="destroy",
            instance_id=instance_id,
            state="deleted",
            message="Instance deleted.",
        )

    def _build_reservation_policy(self, reservation_id: str | None) -> Any:
        modules = self._load_modules()
        if reservation_id:
            return modules.pb2.ReservationPolicy(
                policy=modules.pb2.STRICT,
                reservation_ids=[reservation_id],
            )
        return modules.pb2.ReservationPolicy(policy=modules.pb2.AUTO)

    def _wait_for_instance_state(self, instance_id: str, desired_states: set[str]) -> InstanceRecord:
        deadline = time.monotonic() + self.config.poll_timeout_seconds
        last_state = None

        while time.monotonic() < deadline:
            instance = self.get_instance(instance_id)
            last_state = instance.state
            if instance.state in desired_states:
                return instance
            time.sleep(self.config.poll_interval_seconds)

        raise ProviderError(
            self.name,
            f"Timed out waiting for instance {instance_id} to reach {sorted(desired_states)}.",
            code="timeout",
        )

    def _wait_until_deleted(self, instance_id: str) -> None:
        deadline = time.monotonic() + self.config.poll_timeout_seconds

        while time.monotonic() < deadline:
            try:
                self.get_instance(instance_id)
            except ProviderError as exc:
                if exc.code == "not_found":
                    return
                raise
            time.sleep(self.config.poll_interval_seconds)

        raise ProviderError(self.name, f"Timed out waiting for instance {instance_id} to be deleted.", code="timeout")

    def _normalize_instance(self, item: Any) -> InstanceRecord:
        modules = self._load_modules()
        platform = item.spec.resources.platform
        preset = item.spec.resources.preset
        canonical_gpu = REVERSE_GPU_MAP.get((platform, preset), f"{platform}/{preset}")
        state_name = modules.pb2.InstanceState.Name(item.status.state)
        first_nic = item.status.network_interfaces[0] if item.status.network_interfaces else None
        reservation_id = item.status.reservation_id or None
        raw = modules.message_to_dict(item, preserving_proto_field_name=True)
        return InstanceRecord(
            provider=self.name,
            id=item.metadata.id,
            name=item.metadata.name,
            gpu=canonical_gpu,
            provider_gpu=f"{platform}/{preset}",
            region="global",
            provider_region=item.metadata.parent_id,
            state=STATE_MAP.get(state_name, state_name.lower()),
            public_ip=first_nic.public_ip_address if first_nic else None,
            private_ip=first_nic.ip_address if first_nic else None,
            reservation_id=reservation_id,
            billing_type="reserved" if reservation_id else "on_demand",
            raw=raw,
        )

    def _map_gpu(self, gpu: str) -> tuple[str, str]:
        mapped = GPU_MAP.get(gpu)
        if not mapped:
            raise ProviderError(self.name, f"GPU type '{gpu}' is not supported by Nebius.", code="unsupported")
        return mapped

    def _metadata(self) -> list[tuple[str, str]]:
        return [("authorization", f"Bearer {self.config.api_key}")]

    def _rpc(self, method: Any, request: Any) -> Any:
        modules = self._load_modules()
        try:
            return method(request, metadata=self._metadata())
        except modules.grpc.RpcError as exc:
            raise self._map_grpc_error(exc) from exc

    def _endpoint(self) -> str:
        if self.config.endpoint.startswith("grpc://"):
            return self.config.endpoint[len("grpc://") :]
        return self.config.endpoint

    def _get_instance_stub(self) -> Any:
        modules = self._load_modules()
        if self._instance_stub is None:
            self._channel = modules.grpc.insecure_channel(self._endpoint())
            self._instance_stub = modules.service_pb2_grpc.InstanceServiceStub(self._channel)
        return self._instance_stub

    def _load_modules(self) -> _NebiusModules:
        if self._modules is not None:
            return self._modules

        repo_root = Path(__file__).resolve().parents[3]
        generated_dir = repo_root / "mock_servers" / "generated"
        if not generated_dir.exists():
            raise ProviderError(
                self.name,
                f"Generated Nebius protobufs were not found at {generated_dir}.",
                code="configuration",
            )

        generated_path = str(generated_dir)
        if generated_path not in sys.path:
            sys.path.insert(0, generated_path)

        try:
            grpc = import_module("grpc")
            pb2 = import_module("nebius.compute.v1.instance_pb2")
            service_pb2 = import_module("nebius.compute.v1.instance_service_pb2")
            service_pb2_grpc = import_module("nebius.compute.v1.instance_service_pb2_grpc")
            json_format = import_module("google.protobuf.json_format")
        except Exception as exc:  # pragma: no cover - exercised in environments without grpcio
            raise ProviderError(
                self.name,
                "Nebius provider requires grpcio and protobuf. Install `candidate/requirements.txt` or use the mock server venv.",
                code="configuration",
            ) from exc

        self._modules = _NebiusModules(
            grpc=grpc,
            pb2=pb2,
            service_pb2=service_pb2,
            service_pb2_grpc=service_pb2_grpc,
            message_to_dict=json_format.MessageToDict,
        )
        return self._modules

    def _map_grpc_error(self, exc: Any) -> ProviderError:
        code = None
        details = "Nebius RPC failed"

        if hasattr(exc, "code"):
            status = exc.code()
            code = status.name if hasattr(status, "name") else str(status)
        if hasattr(exc, "details"):
            details = exc.details() or details

        return ProviderError(self.name, details, code=code)
