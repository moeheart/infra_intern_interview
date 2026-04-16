from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CreateRequest:
    provider: str
    gpu: str
    count: int
    name: str | None
    region: str | None
    ssh_key: str
    reservation_id: str | None = None


@dataclass
class InstanceRecord:
    provider: str
    id: str
    name: str
    gpu: str
    provider_gpu: str
    region: str
    provider_region: str
    state: str
    public_ip: str | None
    private_ip: str | None
    reservation_id: str | None
    billing_type: str | None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        return data


@dataclass
class ActionResult:
    provider: str
    action: str
    instance_id: str
    state: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
