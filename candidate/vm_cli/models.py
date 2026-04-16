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


@dataclass
class CapacityRecord:
    provider: str
    region: str
    gpu: str
    available: int | None
    certainty: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FleetMember:
    provider: str
    instance_id: str
    name: str
    region: str
    state: str
    billing_type: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FleetRecord:
    name: str
    gpu: str
    requested_count: int
    status: str
    created_at: str
    instances: list[FleetMember] = field(default_factory=list)
    last_error: str | None = None

    @property
    def tracked_count(self) -> int:
        return len(self.instances)

    def to_summary(self) -> "FleetSummary":
        return FleetSummary(
            name=self.name,
            gpu=self.gpu,
            requested_count=self.requested_count,
            tracked_count=self.tracked_count,
            status=self.status,
            created_at=self.created_at,
            last_error=self.last_error,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "gpu": self.gpu,
            "requested_count": self.requested_count,
            "tracked_count": self.tracked_count,
            "status": self.status,
            "created_at": self.created_at,
            "instances": [instance.to_dict() for instance in self.instances],
            "last_error": self.last_error,
        }


@dataclass
class FleetSummary:
    name: str
    gpu: str
    requested_count: int
    tracked_count: int
    status: str
    created_at: str
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FleetDestroyResult:
    name: str
    status: str
    deleted_count: int
    remaining_count: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
