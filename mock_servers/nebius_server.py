"""
Nebius AI Cloud API Mock Server (gRPC)
Port: 50051

Follows: https://github.com/nebius/api (protobuf/gRPC)
Key traits:
  - Native gRPC interface (not REST)
  - Parent-scoped resources
  - Long-running operations with polling
  - resource_version for optimistic concurrency
  - ReservationPolicy: AUTO / FORBID / STRICT
  - Candidates interact via grpcurl or a gRPC client library
"""

import sys
import os
import uuid
import time
import threading
from concurrent import futures

# Add generated code to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "generated"))

import grpc
from grpc_reflection.v1alpha import reflection

from nebius.compute.v1 import instance_pb2
from nebius.compute.v1 import instance_service_pb2
from nebius.compute.v1 import instance_service_pb2_grpc
from google.protobuf import timestamp_pb2

# --- Auth Interceptor ---
VALID_API_KEYS = {"nebius-test-key-001", "nebius-test-key-002"}


class AuthInterceptor(grpc.ServerInterceptor):
    def intercept_service(self, continuation, handler_call_details):
        metadata = dict(handler_call_details.invocation_metadata)
        token = metadata.get("authorization", "")
        token = token.replace("Bearer ", "")
        if token not in VALID_API_KEYS:
            def abort(request, context):
                context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid or missing API key")
            return grpc.unary_unary_rpc_method_handler(abort)
        return continuation(handler_call_details)


# --- Data ---
VALID_PARENTS = {"project-e1a2b3c4", "project-f5d6e7f8"}

PLATFORMS = {
    "gpu-h100-sxm": {
        "1gpu-16vcpu-200gb": {"vcpu_count": 16, "memory_gibibytes": 200, "gpu_count": 1},
        "8gpu-160vcpu-1600gb": {"vcpu_count": 160, "memory_gibibytes": 1600, "gpu_count": 8},
    },
    "gpu-h200-sxm": {
        "1gpu-20vcpu-256gb": {"vcpu_count": 20, "memory_gibibytes": 256, "gpu_count": 1},
        "8gpu-160vcpu-2048gb": {"vcpu_count": 160, "memory_gibibytes": 2048, "gpu_count": 8},
    },
}

# Capacity: {parent_id: {platform/preset: available_count}}
CAPACITY = {
    "project-e1a2b3c4": {
        "gpu-h100-sxm/1gpu-16vcpu-200gb": 6,
        "gpu-h100-sxm/8gpu-160vcpu-1600gb": 2,
        "gpu-h200-sxm/1gpu-20vcpu-256gb": 4,
        "gpu-h200-sxm/8gpu-160vcpu-2048gb": 1,
    },
    "project-f5d6e7f8": {
        "gpu-h100-sxm/1gpu-16vcpu-200gb": 3,
        "gpu-h100-sxm/8gpu-160vcpu-1600gb": 1,
        "gpu-h200-sxm/1gpu-20vcpu-256gb": 2,
        "gpu-h200-sxm/8gpu-160vcpu-2048gb": 0,
    },
}

# {parent_id: {instance_id: Instance protobuf dict}}
instances: dict[str, dict[str, dict]] = {pid: {} for pid in VALID_PARENTS}

# {operation_id: Operation dict}
all_operations: dict[str, dict] = {}

# Reservations
reservations_store: dict[str, dict[str, dict]] = {pid: {} for pid in VALID_PARENTS}


def _now():
    t = timestamp_pb2.Timestamp()
    t.GetCurrentTime()
    return t


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _create_operation(parent_id, resource_id, method):
    op_id = str(uuid.uuid4())
    op = {
        "id": op_id,
        "parent_id": parent_id,
        "resource_id": resource_id,
        "description": f"{method} instance {resource_id}",
        "done": False,
        "metadata": {"service": "InstanceService", "method": method},
    }
    all_operations[op_id] = op

    def complete():
        time.sleep(2)
        op["done"] = True

    threading.Thread(target=complete, daemon=True).start()
    return op


def _dict_to_operation(d):
    op = instance_service_pb2.Operation(
        id=d["id"],
        parent_id=d["parent_id"],
        resource_id=d["resource_id"],
        description=d["description"],
        done=d["done"],
        metadata=instance_service_pb2.OperationMetadata(
            service=d["metadata"]["service"],
            method=d["metadata"]["method"],
        ),
    )
    return op


def _dict_to_instance(d):
    """Convert internal dict to Instance protobuf."""
    md = d["metadata"]
    metadata = instance_pb2.ResourceMetadata(
        id=md["id"],
        parent_id=md["parent_id"],
        name=md["name"],
        resource_version=md["resource_version"],
        labels=md.get("labels", {}),
    )
    metadata.created_at.FromJsonString(md["created_at"])
    metadata.updated_at.FromJsonString(md["updated_at"])

    sp = d["spec"]
    res_spec = instance_pb2.ResourcesSpec(platform=sp["resources"]["platform"], preset=sp["resources"]["preset"])

    net_specs = []
    for n in sp.get("network_interfaces", []):
        net_specs.append(instance_pb2.NetworkInterfaceSpec(
            name=n["name"], subnet_id=n["subnet_id"], ip_address=n.get("ip_address", ""),
        ))

    reservation_policy = None
    if "reservation_policy" in sp:
        rp = sp["reservation_policy"]
        reservation_policy = instance_pb2.ReservationPolicy(
            policy=rp.get("policy", 0),
            reservation_ids=rp.get("reservation_ids", []),
        )

    spec = instance_pb2.InstanceSpec(
        resources=res_spec,
        network_interfaces=net_specs,
        cloud_init_user_data=sp.get("cloud_init_user_data", ""),
        stopped=sp.get("stopped", False),
        reservation_policy=reservation_policy,
    )

    if sp.get("boot_disk"):
        spec.boot_disk.CopyFrom(instance_pb2.AttachedDiskSpec(
            existing_disk_id=sp["boot_disk"]["existing_disk_id"],
            attach_mode=sp["boot_disk"]["attach_mode"],
        ))

    st = d["status"]
    state_map = {
        "CREATING": instance_pb2.CREATING,
        "RUNNING": instance_pb2.RUNNING,
        "STOPPING": instance_pb2.STOPPING,
        "STOPPED": instance_pb2.STOPPED,
        "STARTING": instance_pb2.STARTING,
        "DELETING": instance_pb2.DELETING,
        "ERROR": instance_pb2.ERROR,
    }

    net_statuses = []
    for ns in st.get("network_interfaces", []):
        net_statuses.append(instance_pb2.NetworkInterfaceStatus(
            name=ns["name"],
            ip_address=ns["ip_address"],
            public_ip_address=ns.get("public_ip_address", ""),
        ))

    status = instance_pb2.InstanceStatus(
        state=state_map.get(st["state"], instance_pb2.STATE_UNSPECIFIED),
        network_interfaces=net_statuses,
        reconciling=st.get("reconciling", False),
        reservation_id=st.get("reservation_id", ""),
    )

    return instance_pb2.Instance(metadata=metadata, spec=spec, status=status)


def _dict_to_reservation(d):
    md = d["metadata"]
    metadata = instance_pb2.ResourceMetadata(
        id=md["id"],
        parent_id=md["parent_id"],
        name=md["name"],
        resource_version=md.get("resource_version", 1),
        labels=md.get("labels", {}),
    )
    metadata.created_at.FromJsonString(md["created_at"])
    metadata.updated_at.FromJsonString(md["updated_at"])

    sp = d["spec"]
    spec = instance_pb2.ReservationSpec(
        platform=sp["platform"],
        preset=sp["preset"],
        total_units=sp["total_units"],
    )

    state_map = {
        "ACTIVE": instance_pb2.RESERVATION_ACTIVE,
        "EXPIRED": instance_pb2.RESERVATION_EXPIRED,
        "PENDING": instance_pb2.RESERVATION_PENDING,
    }
    st = d["status"]
    status = instance_pb2.ReservationStatus(
        state=state_map.get(st["state"], instance_pb2.RESERVATION_STATE_UNSPECIFIED),
        used_units=st["used_units"],
        available_units=st["available_units"],
    )

    return instance_pb2.Reservation(metadata=metadata, spec=spec, status=status)


# --- Seed Data ---

def _seed():
    pid = "project-e1a2b3c4"

    # Seed reservations
    reservations_store[pid]["rsv-neb-001"] = {
        "metadata": {
            "id": "rsv-neb-001", "parent_id": pid, "name": "h100-8gpu-reserved",
            "resource_version": 1, "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-03-15T00:00:00Z",
            "labels": {"team": "ml"},
        },
        "spec": {"platform": "gpu-h100-sxm", "preset": "8gpu-160vcpu-1600gb", "total_units": 3},
        "status": {"state": "ACTIVE", "used_units": 1, "available_units": 2},
    }
    reservations_store[pid]["rsv-neb-002"] = {
        "metadata": {
            "id": "rsv-neb-002", "parent_id": pid, "name": "h200-8gpu-reserved",
            "resource_version": 1, "created_at": "2026-02-01T00:00:00Z", "updated_at": "2026-03-15T00:00:00Z",
            "labels": {},
        },
        "spec": {"platform": "gpu-h200-sxm", "preset": "8gpu-160vcpu-2048gb", "total_units": 2},
        "status": {"state": "ACTIVE", "used_units": 0, "available_units": 2},
    }

    # Seed instance (on a reservation)
    iid = str(uuid.uuid4())
    instances[pid][iid] = {
        "metadata": {
            "id": iid, "parent_id": pid, "name": "train-node-01",
            "resource_version": 1,
            "created_at": "2026-03-15T08:00:00Z", "updated_at": "2026-03-15T08:00:00Z",
            "labels": {"team": "ml", "env": "prod"},
        },
        "spec": {
            "resources": {"platform": "gpu-h100-sxm", "preset": "8gpu-160vcpu-1600gb"},
            "network_interfaces": [{"name": "eth0", "subnet_id": "subnet-001", "ip_address": "10.0.0.10"}],
            "boot_disk": {"existing_disk_id": "disk-001", "attach_mode": "READ_WRITE"},
            "cloud_init_user_data": "",
            "stopped": False,
            "reservation_policy": {"policy": 0, "reservation_ids": []},  # AUTO
        },
        "status": {
            "state": "RUNNING",
            "network_interfaces": [
                {"name": "eth0", "ip_address": "10.0.0.10", "public_ip_address": "185.0.0.10"}
            ],
            "reconciling": False,
            "reservation_id": "rsv-neb-001",
        },
    }

_seed()


def _find_reservation(parent_id, platform, preset, policy_type, reservation_ids):
    """Find a reservation based on policy."""
    rsvs = reservations_store.get(parent_id, {})

    if policy_type == 1:  # FORBID
        return None

    candidates = []
    for rsv in rsvs.values():
        if rsv["status"]["state"] != "ACTIVE":
            continue
        if rsv["spec"]["platform"] != platform or rsv["spec"]["preset"] != preset:
            continue
        if rsv["status"]["available_units"] <= 0:
            continue
        if policy_type == 2 and rsv["metadata"]["id"] not in reservation_ids:  # STRICT
            continue
        candidates.append(rsv)

    if policy_type == 2 and not candidates:  # STRICT but none found
        return "STRICT_FAIL"

    return candidates[0] if candidates else None


# --- gRPC Service Implementation ---

class InstanceServiceServicer(instance_service_pb2_grpc.InstanceServiceServicer):

    def Get(self, request, context):
        for pid, insts in instances.items():
            if request.id in insts:
                return _dict_to_instance(insts[request.id])
        context.abort(grpc.StatusCode.NOT_FOUND, f"Instance {request.id} not found")

    def List(self, request, context):
        parent_id = request.parent_id
        if parent_id not in VALID_PARENTS:
            context.abort(grpc.StatusCode.PERMISSION_DENIED, f"Parent {parent_id} not found")

        items = [_dict_to_instance(d) for d in instances[parent_id].values()]
        return instance_service_pb2.ListInstancesResponse(instances=items, next_page_token="")

    def Create(self, request, context):
        parent_id = request.metadata.parent_id
        if parent_id not in VALID_PARENTS:
            context.abort(grpc.StatusCode.PERMISSION_DENIED, f"Parent {parent_id} not found")

        platform = request.spec.resources.platform
        preset = request.spec.resources.preset

        if platform not in PLATFORMS:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"Unknown platform: {platform}")
        if preset not in PLATFORMS[platform]:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"Unknown preset: {preset}")

        cap_key = f"{platform}/{preset}"

        # Check reservation policy
        policy_type = 0  # AUTO default
        reservation_ids = []
        if request.spec.HasField("reservation_policy"):
            policy_type = request.spec.reservation_policy.policy
            reservation_ids = list(request.spec.reservation_policy.reservation_ids)

        rsv = _find_reservation(parent_id, platform, preset, policy_type, reservation_ids)
        reservation_id = ""

        if rsv == "STRICT_FAIL":
            context.abort(grpc.StatusCode.FAILED_PRECONDITION,
                          "No matching reservation available for STRICT policy")
        elif rsv:
            rsv["status"]["used_units"] += 1
            rsv["status"]["available_units"] -= 1
            reservation_id = rsv["metadata"]["id"]
        else:
            # On-demand
            available = CAPACITY.get(parent_id, {}).get(cap_key, 0)
            if available <= 0:
                context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED,
                              f"No capacity for {cap_key} in {parent_id}")
            CAPACITY[parent_id][cap_key] -= 1

        iid = str(uuid.uuid4())
        now = _now_iso()

        subnet = "subnet-default"
        if request.spec.network_interfaces:
            subnet = request.spec.network_interfaces[0].subnet_id

        instance = {
            "metadata": {
                "id": iid, "parent_id": parent_id, "name": request.metadata.name,
                "resource_version": 1, "created_at": now, "updated_at": now,
                "labels": dict(request.metadata.labels),
            },
            "spec": {
                "resources": {"platform": platform, "preset": preset},
                "network_interfaces": [
                    {"name": "eth0", "subnet_id": subnet,
                     "ip_address": f"10.0.0.{20 + len(instances[parent_id])}"}
                ],
                "boot_disk": {"existing_disk_id": f"disk-{iid[:8]}", "attach_mode": "READ_WRITE"},
                "cloud_init_user_data": request.spec.cloud_init_user_data,
                "stopped": request.spec.stopped,
                "reservation_policy": {"policy": policy_type, "reservation_ids": reservation_ids},
            },
            "status": {
                "state": "CREATING",
                "network_interfaces": [],
                "reconciling": True,
                "reservation_id": reservation_id,
            },
        }
        instances[parent_id][iid] = instance
        op = _create_operation(parent_id, iid, "Create")

        def set_running():
            time.sleep(3)
            if iid in instances[parent_id]:
                inst = instances[parent_id][iid]
                inst["status"]["state"] = "RUNNING"
                inst["status"]["reconciling"] = False
                inst["status"]["network_interfaces"] = [
                    {"name": "eth0",
                     "ip_address": inst["spec"]["network_interfaces"][0]["ip_address"],
                     "public_ip_address": f"185.0.0.{20 + len(instances[parent_id])}"}
                ]

        threading.Thread(target=set_running, daemon=True).start()
        return _dict_to_operation(op)

    def Delete(self, request, context):
        for pid, insts in instances.items():
            if request.id in insts:
                inst = insts[request.id]
                inst["status"]["state"] = "DELETING"
                inst["status"]["reconciling"] = True
                op = _create_operation(pid, request.id, "Delete")

                def do_delete():
                    time.sleep(2)
                    if request.id in instances[pid]:
                        deleted = instances[pid].pop(request.id)
                        cap_key = f"{deleted['spec']['resources']['platform']}/{deleted['spec']['resources']['preset']}"
                        rsv_id = deleted["status"].get("reservation_id", "")
                        if rsv_id and rsv_id in reservations_store.get(pid, {}):
                            rsv = reservations_store[pid][rsv_id]
                            rsv["status"]["used_units"] = max(0, rsv["status"]["used_units"] - 1)
                            rsv["status"]["available_units"] = rsv["spec"]["total_units"] - rsv["status"]["used_units"]
                        else:
                            CAPACITY[pid][cap_key] = CAPACITY.get(pid, {}).get(cap_key, 0) + 1

                threading.Thread(target=do_delete, daemon=True).start()
                return _dict_to_operation(op)

        context.abort(grpc.StatusCode.NOT_FOUND, f"Instance {request.id} not found")

    def Start(self, request, context):
        for pid, insts in instances.items():
            if request.id in insts:
                inst = insts[request.id]
                if inst["status"]["state"] != "STOPPED":
                    context.abort(grpc.StatusCode.FAILED_PRECONDITION, "Instance not stopped")
                inst["status"]["state"] = "STARTING"
                inst["status"]["reconciling"] = True
                op = _create_operation(pid, request.id, "Start")

                def set_running():
                    time.sleep(2)
                    if request.id in instances[pid]:
                        instances[pid][request.id]["status"]["state"] = "RUNNING"
                        instances[pid][request.id]["status"]["reconciling"] = False

                threading.Thread(target=set_running, daemon=True).start()
                return _dict_to_operation(op)

        context.abort(grpc.StatusCode.NOT_FOUND, f"Instance {request.id} not found")

    def Stop(self, request, context):
        for pid, insts in instances.items():
            if request.id in insts:
                inst = insts[request.id]
                if inst["status"]["state"] != "RUNNING":
                    context.abort(grpc.StatusCode.FAILED_PRECONDITION, "Instance not running")
                inst["status"]["state"] = "STOPPING"
                inst["status"]["reconciling"] = True
                op = _create_operation(pid, request.id, "Stop")

                def set_stopped():
                    time.sleep(2)
                    if request.id in instances[pid]:
                        instances[pid][request.id]["status"]["state"] = "STOPPED"
                        instances[pid][request.id]["status"]["reconciling"] = False
                        # Release reservation
                        rsv_id = instances[pid][request.id]["status"].get("reservation_id", "")
                        if rsv_id and rsv_id in reservations_store.get(pid, {}):
                            rsv = reservations_store[pid][rsv_id]
                            rsv["status"]["used_units"] = max(0, rsv["status"]["used_units"] - 1)
                            rsv["status"]["available_units"] = rsv["spec"]["total_units"] - rsv["status"]["used_units"]

                threading.Thread(target=set_stopped, daemon=True).start()
                return _dict_to_operation(op)

        context.abort(grpc.StatusCode.NOT_FOUND, f"Instance {request.id} not found")


class ReservationServiceServicer(instance_service_pb2_grpc.ReservationServiceServicer):

    def Get(self, request, context):
        for pid, rsvs in reservations_store.items():
            if request.id in rsvs:
                return _dict_to_reservation(rsvs[request.id])
        context.abort(grpc.StatusCode.NOT_FOUND, f"Reservation {request.id} not found")

    def List(self, request, context):
        parent_id = request.parent_id
        if parent_id not in VALID_PARENTS:
            context.abort(grpc.StatusCode.PERMISSION_DENIED, f"Parent {parent_id} not found")
        items = [_dict_to_reservation(d) for d in reservations_store[parent_id].values()]
        return instance_service_pb2.ListReservationsResponse(reservations=items, next_page_token="")


def serve():
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[AuthInterceptor()],
    )
    instance_service_pb2_grpc.add_InstanceServiceServicer_to_server(InstanceServiceServicer(), server)
    instance_service_pb2_grpc.add_ReservationServiceServicer_to_server(ReservationServiceServicer(), server)

    # Enable reflection for grpcurl discovery
    service_names = (
        instance_service_pb2.DESCRIPTOR.services_by_name["InstanceService"].full_name,
        instance_service_pb2.DESCRIPTOR.services_by_name["ReservationService"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)

    server.add_insecure_port("[::]:50051")
    print(f"Nebius gRPC server listening on port 50051")
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
