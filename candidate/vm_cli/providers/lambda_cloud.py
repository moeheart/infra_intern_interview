from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from vm_cli.config import LambdaConfig
from vm_cli.errors import ProviderError, UnsupportedOperationError
from vm_cli.http import HttpError, request_json
from vm_cli.models import ActionResult, CapacityRecord, CreateRequest, InstanceRecord
from vm_cli.providers.base import VMProvider

GPU_MAP = {
    "a100.1x": "gpu_1x_a100",
    "a100.8x": "gpu_8x_a100",
    "h100.1x": "gpu_1x_h100",
    "h100.8x": "gpu_8x_h100",
}
REGION_MAP = {
    "us-west": "us-west-1",
    "us-west-1": "us-west-1",
    "us-east": "us-east-1",
    "us-east-1": "us-east-1",
    "eu-west": "eu-west-1",
    "eu-west-1": "eu-west-1",
}
CANONICAL_REGION_NAMES = {
    "us-west-1": "us-west",
    "us-east-1": "us-east",
    "eu-west-1": "eu-west",
}
REVERSE_GPU_MAP = {value: key for key, value in GPU_MAP.items()}
STATE_MAP = {
    "booting": "creating",
    "active": "running",
    "unhealthy": "error",
    "terminated": "terminated",
    "terminating": "deleting",
}


class LambdaProvider(VMProvider):
    name = "lambda"

    def __init__(self, config: LambdaConfig) -> None:
        self.config = config

    def list_instances(self) -> list[InstanceRecord]:
        payload = self._request("GET", self._url("/instances"))
        return [self._normalize_instance(item) for item in payload.get("data", [])]

    def get_instance(self, instance_id: str) -> InstanceRecord:
        payload = self._request("GET", self._url(f"/instances/{instance_id}"))
        return self._normalize_instance(payload["data"])

    def create_instances(self, req: CreateRequest) -> list[InstanceRecord]:
        body = {
            "region_name": self._map_region(req.region or "us-west"),
            "instance_type_name": self._map_gpu(req.gpu),
            "ssh_key_names": [req.ssh_key],
            "name": req.name or f"{self.name}-{req.gpu}",
            "quantity": req.count,
        }
        if req.reservation_id:
            body["reservation_id"] = req.reservation_id

        payload = self._request("POST", self._url("/instance-operations/launch"), json_body=body)
        instance_ids = payload["data"]["instance_ids"]
        return [self.get_instance(instance_id) for instance_id in instance_ids]

    def list_capacity(self, gpu: str) -> list[CapacityRecord]:
        mapped_gpu = GPU_MAP.get(gpu)
        if not mapped_gpu:
            return []

        payload = self._request("GET", self._url("/instance-types"))
        type_payload = payload.get("data", {}).get(mapped_gpu, {})
        records: list[CapacityRecord] = []
        for region in type_payload.get("regions_with_capacity_available", []):
            provider_region = region["name"]
            records.append(
                CapacityRecord(
                    provider=self.name,
                    region=CANONICAL_REGION_NAMES.get(provider_region, provider_region),
                    gpu=gpu,
                    available=None,
                    certainty="unknown",
                )
            )
        return records

    def create_instances_best_effort(self, req: CreateRequest) -> list[InstanceRecord]:
        try:
            return self.create_instances(req)
        except ProviderError as exc:
            if exc.code != "capacity":
                raise

            available = _extract_available_capacity(exc.message)
            if available <= 0:
                return []

            retry_req = replace(req, count=min(req.count, available))
            try:
                return self.create_instances(retry_req)
            except ProviderError as retry_exc:
                if retry_exc.code == "capacity":
                    return []
                raise

    def stop_instance(self, instance_id: str) -> ActionResult:
        raise UnsupportedOperationError(self.name, "stop")

    def start_instance(self, instance_id: str) -> ActionResult:
        raise UnsupportedOperationError(self.name, "start")

    def destroy_instance(self, instance_id: str) -> ActionResult:
        self._request(
            "POST",
            self._url("/instance-operations/terminate"),
            json_body={"instance_ids": [instance_id]},
        )
        return ActionResult(
            provider=self.name,
            action="destroy",
            instance_id=instance_id,
            state="deleted",
            message="Instance terminated.",
        )

    def _normalize_instance(self, item: dict[str, Any]) -> InstanceRecord:
        provider_region = item["region"]["name"]
        provider_gpu = item["instance_type"]["name"]
        return InstanceRecord(
            provider=self.name,
            id=item["id"],
            name=item["name"],
            gpu=REVERSE_GPU_MAP.get(provider_gpu, provider_gpu),
            provider_gpu=provider_gpu,
            region=CANONICAL_REGION_NAMES.get(provider_region, provider_region),
            provider_region=provider_region,
            state=STATE_MAP.get(item["status"], item["status"]),
            public_ip=item.get("ip"),
            private_ip=item.get("private_ip"),
            reservation_id=item.get("reservation_id"),
            billing_type="reserved" if item.get("is_reserved") else "on_demand",
            raw=item,
        )

    def _map_gpu(self, gpu: str) -> str:
        mapped = GPU_MAP.get(gpu)
        if not mapped:
            raise ProviderError(self.name, f"GPU type '{gpu}' is not supported by Lambda.", code="unsupported")
        return mapped

    def _map_region(self, region: str) -> str:
        mapped = REGION_MAP.get(region)
        if not mapped:
            raise ProviderError(self.name, f"Region '{region}' is not supported by Lambda.", code="unsupported")
        return mapped

    def _url(self, path: str) -> str:
        return f"{self.config.base_url}/api/v1{path}"

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
            raise _map_lambda_error(exc) from exc
        except TimeoutError as exc:
            raise ProviderError(self.name, "Timed out while waiting for Lambda.", code="timeout") from exc
        except ConnectionError as exc:
            raise ProviderError(self.name, "Unable to reach Lambda.", code="network") from exc


def _map_lambda_error(exc: HttpError) -> ProviderError:
    payload = exc.payload if isinstance(exc.payload, dict) else {}
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else payload
    error_block = detail.get("error") if isinstance(detail.get("error"), dict) else detail
    return ProviderError(
        "lambda",
        error_block.get("message", exc.raw_body or "Lambda request failed"),
        code=error_block.get("code"),
        status=exc.status,
        suggestion=error_block.get("suggestion"),
    )


def _extract_available_capacity(message: str) -> int:
    match = re.search(r"available\s+(\d+)", message)
    if match:
        return int(match.group(1))
    return 0
