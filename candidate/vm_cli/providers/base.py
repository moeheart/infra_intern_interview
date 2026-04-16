from __future__ import annotations

from abc import ABC, abstractmethod

from vm_cli.models import ActionResult, CapacityRecord, CreateRequest, InstanceRecord


class VMProvider(ABC):
    name: str

    @abstractmethod
    def list_instances(self) -> list[InstanceRecord]:
        raise NotImplementedError

    @abstractmethod
    def get_instance(self, instance_id: str) -> InstanceRecord:
        raise NotImplementedError

    @abstractmethod
    def create_instances(self, req: CreateRequest) -> list[InstanceRecord]:
        raise NotImplementedError

    @abstractmethod
    def list_capacity(self, gpu: str) -> list[CapacityRecord]:
        raise NotImplementedError

    @abstractmethod
    def create_instances_best_effort(self, req: CreateRequest) -> list[InstanceRecord]:
        raise NotImplementedError

    @abstractmethod
    def stop_instance(self, instance_id: str) -> ActionResult:
        raise NotImplementedError

    @abstractmethod
    def start_instance(self, instance_id: str) -> ActionResult:
        raise NotImplementedError

    @abstractmethod
    def destroy_instance(self, instance_id: str) -> ActionResult:
        raise NotImplementedError
