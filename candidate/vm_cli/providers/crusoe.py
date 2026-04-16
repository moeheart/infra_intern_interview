from __future__ import annotations

import time
from typing import Any

from vm_cli.config import CrusoeConfig
from vm_cli.errors import ProviderError
from vm_cli.http import HttpError, request_json
from vm_cli.models import ActionResult, CreateRequest, InstanceRecord
from vm_cli.providers.base import VMProvider

CANONICAL_GPU_CHOICES = {"a100.1x", "a100.8x", "h100.1x", "h100.8x"}
CANONICAL_REGION_ALIASES = {
    "us-west": "us-west1",
    "us-west1": "us-west1",
    "us-east": "us-east1",
    "us-east1": "us-east1",
    "eu-west": "eu-west1",
    "eu-west1": "eu-west1",
}
CANONICAL_REGION_NAMES = {
    "us-west1": "us-west",
    "us-east1": "us-east",
    "eu-west1": "eu-west",
}
STATE_MAP = {
    "STATE_CREATING": "creating",
    "STATE_RUNNING": "running",
    "STATE_STOPPING": "stopping",
    "STATE_STOPPED": "stopped",
    "STATE_STARTING": "starting",
    "STATE_DELETING": "deleting",
}


class CrusoeProvider(VMProvider):
    name = "crusoe"

    def __init__(self, config: CrusoeConfig) -> None:
        self.config = config

    def list_instances(self) -> list[InstanceRecord]:
        payload = self._request("GET", self._instances_url())
        return [self._normalize_instance(item) for item in payload.get("items", [])]

    def get_instance(self, instance_id: str) -> InstanceRecord:
        payload = self._request("GET", f"{self._instances_url()}/{instance_id}")
        return self._normalize_instance(payload)

    def create_instances(self, req: CreateRequest) -> list[InstanceRecord]:
        region = self._map_region(req.region or "us-west")
        gpu = self._map_gpu(req.gpu)
        instances: list[InstanceRecord] = []
        base_name = req.name or f"{self.name}-{req.gpu}"

        for index in range(req.count):
            name = base_name if req.count == 1 else f"{base_name}-{index + 1}"
            body = {
                "name": name,
                "type": gpu,
                "location": region,
                "ssh_key": req.ssh_key,
            }
            if req.reservation_id:
                body["reservation_id"] = req.reservation_id

            payload = self._request("POST", self._instances_url(), json_body=body)
            operation_id = payload["operation"]["operation_id"]
            resource_id = payload["instance"]["id"]
            self._poll_operation(operation_id)
            instances.append(self.get_instance(resource_id))

        return instances

    def stop_instance(self, instance_id: str) -> ActionResult:
        return self._run_instance_action(instance_id, "STOP")

    def start_instance(self, instance_id: str) -> ActionResult:
        return self._run_instance_action(instance_id, "START")

    def destroy_instance(self, instance_id: str) -> ActionResult:
        payload = self._request("DELETE", f"{self._instances_url()}/{instance_id}")
        operation_id = payload["operation"]["operation_id"]
        self._poll_operation(operation_id)
        return ActionResult(
            provider=self.name,
            action="destroy",
            instance_id=instance_id,
            state="deleted",
            message="Instance deleted.",
        )

    def _run_instance_action(self, instance_id: str, action: str) -> ActionResult:
        payload = self._request(
            "PATCH",
            f"{self._instances_url()}/{instance_id}",
            json_body={"action": action},
        )
        operation_id = payload["operation"]["operation_id"]
        self._poll_operation(operation_id)
        instance = self.get_instance(instance_id)
        return ActionResult(
            provider=self.name,
            action=action.lower(),
            instance_id=instance_id,
            state=instance.state,
            message=f"Instance {action.lower()} completed.",
        )

    def _poll_operation(self, operation_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.poll_timeout_seconds
        last_payload: dict[str, Any] | None = None

        while time.monotonic() < deadline:
            payload = self._request("GET", f"{self._instances_url()}/operations/{operation_id}")
            last_payload = payload
            if payload.get("state") == "SUCCEEDED":
                return payload
            time.sleep(self.config.poll_interval_seconds)

        raise ProviderError(
            self.name,
            f"Timed out waiting for operation {operation_id}.",
            code="timeout",
        )

    def _normalize_instance(self, item: dict[str, Any]) -> InstanceRecord:
        provider_region = item["location"]
        provider_gpu = item["type"]
        return InstanceRecord(
            provider=self.name,
            id=item["id"],
            name=item["name"],
            gpu=provider_gpu,
            provider_gpu=provider_gpu,
            region=CANONICAL_REGION_NAMES.get(provider_region, provider_region),
            provider_region=provider_region,
            state=STATE_MAP.get(item["state"], item["state"].lower()),
            public_ip=item.get("ip_address"),
            private_ip=item.get("private_ip_address"),
            reservation_id=item.get("reservation_id"),
            billing_type=item.get("billing_type"),
            raw=item,
        )

    def _map_gpu(self, gpu: str) -> str:
        if gpu not in CANONICAL_GPU_CHOICES:
            raise ProviderError(self.name, f"GPU type '{gpu}' is not supported by Crusoe.", code="unsupported")
        return gpu

    def _map_region(self, region: str) -> str:
        mapped = CANONICAL_REGION_ALIASES.get(region)
        if not mapped:
            raise ProviderError(self.name, f"Region '{region}' is not supported by Crusoe.", code="unsupported")
        return mapped

    def _instances_url(self) -> str:
        return (
            f"{self.config.base_url}/v1alpha5/projects/"
            f"{self.config.project_id}/compute/vms/instances"
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        try:
            return request_json(method, url, headers=headers, json_body=json_body)
        except HttpError as exc:
            raise _map_crusoe_error(exc) from exc
        except TimeoutError as exc:
            raise ProviderError(self.name, "Timed out while waiting for Crusoe.", code="timeout") from exc
        except ConnectionError as exc:
            raise ProviderError(self.name, "Unable to reach Crusoe.", code="network") from exc


def _map_crusoe_error(exc: HttpError) -> ProviderError:
    payload = exc.payload if isinstance(exc.payload, dict) else {}
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else payload
    return ProviderError(
        "crusoe",
        detail.get("message", exc.raw_body or "Crusoe request failed"),
        code=detail.get("code"),
        status=exc.status,
    )
