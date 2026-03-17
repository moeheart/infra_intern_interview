"""
Crusoe Cloud API Mock Server (v1alpha5)
Port: 8001

Follows: https://api.crusoecloud.com/v1alpha5/openapi.json
Key traits:
  - Project-scoped resources
  - Async operations for all mutations
  - Three distinct restart-family operations: reboot (graceful), reset (hard), restart (stop+start)
  - Reservation system: reserved capacity with auto-placement
"""

import uuid
import time
import threading
from typing import Optional
from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(title="Crusoe Cloud API Mock", version="v1alpha5")

# --- Auth ---
VALID_API_KEYS = {"crusoe-test-key-001", "crusoe-test-key-002"}


def check_auth(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(401, detail={"code": "UNAUTHENTICATED", "message": "Missing authorization header"})
    token = authorization.replace("Bearer ", "")
    if token not in VALID_API_KEYS:
        raise HTTPException(401, detail={"code": "UNAUTHENTICATED", "message": "Invalid API key"})


# --- Data ---
VALID_PROJECTS = {"proj-001", "proj-002"}

VM_TYPES = {
    "a100.1x": {
        "type": "a100.1x", "description": "1x A100 40GB",
        "gpu_type": "A100", "gpu_count": 1, "vcpu_count": 12, "memory_gib": 85,
        "price_per_hour_usd": 1.48,
    },
    "a100.8x": {
        "type": "a100.8x", "description": "8x A100 40GB",
        "gpu_type": "A100", "gpu_count": 8, "vcpu_count": 96, "memory_gib": 680,
        "price_per_hour_usd": 11.84,
    },
    "h100.1x": {
        "type": "h100.1x", "description": "1x H100 80GB",
        "gpu_type": "H100", "gpu_count": 1, "vcpu_count": 16, "memory_gib": 128,
        "price_per_hour_usd": 3.46,
    },
    "h100.8x": {
        "type": "h100.8x", "description": "8x H100 80GB",
        "gpu_type": "H100", "gpu_count": 8, "vcpu_count": 192, "memory_gib": 1440,
        "price_per_hour_usd": 27.68,
    },
}

LOCATIONS = ["us-east1", "us-west1", "eu-west1"]

# On-demand capacity: {location: {vm_type: count}}
ON_DEMAND_CAPACITY = {
    "us-east1": {"a100.1x": 5, "a100.8x": 2, "h100.1x": 8, "h100.8x": 1},
    "us-west1": {"a100.1x": 10, "a100.8x": 3, "h100.1x": 4, "h100.8x": 2},
    "eu-west1": {"a100.1x": 3, "a100.8x": 1, "h100.1x": 2, "h100.8x": 0},
}

# --- Reservations ---
# {project_id: {reservation_id: reservation}}
project_reservations: dict[str, dict[str, dict]] = {pid: {} for pid in VALID_PROJECTS}


def _seed_reservations():
    project_reservations["proj-001"] = {
        "rsv-001": {
            "id": "rsv-001",
            "name": "8x a100-40gb @ $9.50/gpu/hr (rsv-001)",
            "status": "ACTIVE",
            "vm_type": "a100.8x",
            "gpu_type": "A100",
            "location": "us-west1",
            "total_gpus": 16,       # 2 x 8-GPU nodes
            "used_gpus": 8,         # 1 node in use (seeded)
            "unit_price_per_gpu_hour_usd": 9.50,
            "start_date": "2026-01-01T00:00:00Z",
            "end_date": "2026-12-31T23:59:59Z",
        },
        "rsv-002": {
            "id": "rsv-002",
            "name": "8x h100-80gb @ $22.00/gpu/hr (rsv-002)",
            "status": "ACTIVE",
            "vm_type": "h100.8x",
            "gpu_type": "H100",
            "location": "us-west1",
            "total_gpus": 32,       # 4 x 8-GPU nodes
            "used_gpus": 0,
            "unit_price_per_gpu_hour_usd": 22.00,
            "start_date": "2026-02-01T00:00:00Z",
            "end_date": "2027-01-31T23:59:59Z",
        },
        "rsv-003": {
            "id": "rsv-003",
            "name": "1x a100-40gb @ $1.20/gpu/hr (rsv-003)",
            "status": "EXPIRED",
            "vm_type": "a100.1x",
            "gpu_type": "A100",
            "location": "us-east1",
            "total_gpus": 4,
            "used_gpus": 0,
            "unit_price_per_gpu_hour_usd": 1.20,
            "start_date": "2025-06-01T00:00:00Z",
            "end_date": "2025-12-31T23:59:59Z",
        },
    }

_seed_reservations()


def _find_reservation(project_id: str, vm_type: str, location: str, reservation_id: str = None) -> Optional[dict]:
    """Find a matching reservation. If reservation_id given, use that. Otherwise auto-place in cheapest."""
    reservations = project_reservations.get(project_id, {})

    if reservation_id:
        rsv = reservations.get(reservation_id)
        if not rsv:
            return None
        if rsv["status"] != "ACTIVE":
            return None
        if rsv["vm_type"] != vm_type:
            return None
        if rsv.get("location") and rsv["location"] != location:
            return None
        gpu_count = VM_TYPES[vm_type]["gpu_count"]
        if rsv["used_gpus"] + gpu_count > rsv["total_gpus"]:
            return None
        return rsv

    # Auto-place: find cheapest active reservation with space
    candidates = []
    for rsv in reservations.values():
        if rsv["status"] != "ACTIVE":
            continue
        if rsv["vm_type"] != vm_type:
            continue
        if rsv.get("location") and rsv["location"] != location:
            continue
        gpu_count = VM_TYPES[vm_type]["gpu_count"]
        if rsv["used_gpus"] + gpu_count <= rsv["total_gpus"]:
            candidates.append(rsv)

    if not candidates:
        return None

    # Sort by: lowest unit price first, then least available capacity (pack tightly)
    candidates.sort(key=lambda r: (r["unit_price_per_gpu_hour_usd"], -(r["total_gpus"] - r["used_gpus"])))
    return candidates[0]


# --- Instance Store ---
# {project_id: {vm_id: vm_data}}
project_instances: dict[str, dict[str, dict]] = {pid: {} for pid in VALID_PROJECTS}

# {project_id: {operation_id: operation_data}}
operations: dict[str, dict[str, dict]] = {pid: {} for pid in VALID_PROJECTS}


def _check_project(project_id: str):
    if project_id not in VALID_PROJECTS:
        raise HTTPException(403, detail={"code": "PERMISSION_DENIED", "message": f"Project {project_id} not found"})


def _create_operation(project_id: str, resource_id: str, action: str) -> dict:
    op_id = str(uuid.uuid4())
    op = {
        "operation_id": op_id,
        "state": "IN_PROGRESS",
        "resource_id": resource_id,
        "action": action,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "completed_at": None,
    }
    operations[project_id][op_id] = op

    def complete():
        time.sleep(2)
        op["state"] = "SUCCEEDED"
        op["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    threading.Thread(target=complete, daemon=True).start()
    return op


def _seed_instances():
    pid = "proj-001"
    vm_id = str(uuid.uuid4())
    project_instances[pid][vm_id] = {
        "id": vm_id,
        "name": "gpu-worker-1",
        "type": "a100.8x",
        "location": "us-west1",
        "state": "STATE_RUNNING",
        "ip_address": "203.0.113.10",
        "private_ip_address": "10.100.0.10",
        "created_at": "2026-03-15T10:00:00Z",
        "ssh_key": "default-key",
        "startup_script": None,
        "disks": [],
        "reservation_id": "rsv-001",   # This VM is on a reservation
        "billing_type": "reserved",     # "reserved" or "on_demand"
    }

_seed_instances()


# ==========================================
# Reservation Endpoints
# ==========================================

@app.get("/v1alpha5/projects/{project_id}/reservations")
def list_reservations(project_id: str, authorization: Optional[str] = Header(None),
                      status: Optional[str] = Query(None)):
    check_auth(authorization)
    _check_project(project_id)
    reservations = list(project_reservations[project_id].values())
    if status:
        status_filter = set(status.split(","))
        reservations = [r for r in reservations if r["status"] in status_filter]
    return {"items": reservations}


@app.get("/v1alpha5/projects/{project_id}/reservations/{reservation_id}")
def get_reservation(project_id: str, reservation_id: str, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    _check_project(project_id)
    if reservation_id not in project_reservations[project_id]:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": f"Reservation {reservation_id} not found"})
    return project_reservations[project_id][reservation_id]


# ==========================================
# VM Type & Capacity Endpoints
# ==========================================

@app.get("/v1alpha5/projects/{project_id}/compute/vms/types")
def get_vm_types(project_id: str, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    _check_project(project_id)
    items = []
    for vtype, info in VM_TYPES.items():
        available_locations = [loc for loc in LOCATIONS if ON_DEMAND_CAPACITY.get(loc, {}).get(vtype, 0) > 0]
        items.append({**info, "available_locations": available_locations})
    return {"items": items}


@app.get("/v1alpha5/projects/{project_id}/capacity")
def get_capacity(project_id: str, authorization: Optional[str] = Header(None)):
    """Get available capacity including both on-demand and reserved."""
    check_auth(authorization)
    _check_project(project_id)

    capacity = []
    for loc in LOCATIONS:
        for vtype, info in VM_TYPES.items():
            on_demand = ON_DEMAND_CAPACITY.get(loc, {}).get(vtype, 0)

            # Check reserved capacity
            reserved_available = 0
            for rsv in project_reservations[project_id].values():
                if rsv["status"] == "ACTIVE" and rsv["vm_type"] == vtype:
                    if not rsv.get("location") or rsv["location"] == loc:
                        gpu_count = info["gpu_count"]
                        reserved_available += (rsv["total_gpus"] - rsv["used_gpus"]) // gpu_count

            capacity.append({
                "location": loc,
                "vm_type": vtype,
                "on_demand_available": on_demand,
                "reserved_available": reserved_available,
                "total_available": on_demand + reserved_available,
            })

    return {"items": capacity}


# ==========================================
# Instance CRUD Endpoints
# ==========================================

@app.get("/v1alpha5/projects/{project_id}/compute/vms/instances")
def list_instances(
    project_id: str,
    authorization: Optional[str] = Header(None),
    states: Optional[str] = Query(None),
    types: Optional[str] = Query(None),
    locations: Optional[str] = Query(None),
    names: Optional[str] = Query(None),
    limit: Optional[int] = Query(None),
):
    check_auth(authorization)
    _check_project(project_id)

    vms = list(project_instances[project_id].values())
    if states:
        state_filter = set(states.split(","))
        vms = [v for v in vms if v["state"] in state_filter]
    if types:
        type_filter = set(types.split(","))
        vms = [v for v in vms if v["type"] in type_filter]
    if locations:
        loc_filter = set(locations.split(","))
        vms = [v for v in vms if v["location"] in loc_filter]
    if names:
        name_filter = set(names.split(","))
        vms = [v for v in vms if v["name"] in name_filter]
    if limit:
        vms = vms[:limit]

    return {"items": vms}


@app.get("/v1alpha5/projects/{project_id}/compute/vms/instances/{vm_id}")
def get_instance(project_id: str, vm_id: str, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    _check_project(project_id)
    if vm_id not in project_instances[project_id]:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": f"Instance {vm_id} not found"})
    return project_instances[project_id][vm_id]


class CreateInstanceRequest(BaseModel):
    name: str
    type: str
    location: str
    ssh_key: str
    startup_script: Optional[str] = None
    reservation_id: Optional[str] = None   # Explicitly use a reservation, or auto-place


@app.post("/v1alpha5/projects/{project_id}/compute/vms/instances")
def create_instance(project_id: str, body: CreateInstanceRequest, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    _check_project(project_id)

    if body.type not in VM_TYPES:
        raise HTTPException(400, detail={"code": "INVALID_ARGUMENT", "message": f"Unknown VM type: {body.type}"})
    if body.location not in LOCATIONS:
        raise HTTPException(400, detail={"code": "INVALID_ARGUMENT", "message": f"Unknown location: {body.location}"})

    # Try reservation first
    rsv = _find_reservation(project_id, body.type, body.location, body.reservation_id)
    billing_type = "on_demand"
    reservation_id = None

    if rsv:
        gpu_count = VM_TYPES[body.type]["gpu_count"]
        rsv["used_gpus"] += gpu_count
        billing_type = "reserved"
        reservation_id = rsv["id"]
    else:
        if body.reservation_id:
            raise HTTPException(400, detail={
                "code": "FAILED_PRECONDITION",
                "message": f"Reservation {body.reservation_id} not available for {body.type} in {body.location}. "
                           "Check reservation status, type, location, and remaining capacity.",
            })
        # Fall back to on-demand
        available = ON_DEMAND_CAPACITY.get(body.location, {}).get(body.type, 0)
        if available <= 0:
            raise HTTPException(400, detail={
                "code": "RESOURCE_EXHAUSTED",
                "message": f"No capacity for {body.type} in {body.location}. No on-demand or reserved capacity available.",
            })
        ON_DEMAND_CAPACITY[body.location][body.type] -= 1

    vm_id = str(uuid.uuid4())
    vm = {
        "id": vm_id,
        "name": body.name,
        "type": body.type,
        "location": body.location,
        "state": "STATE_CREATING",
        "ip_address": f"203.0.113.{100 + len(project_instances[project_id])}",
        "private_ip_address": f"10.100.0.{100 + len(project_instances[project_id])}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ssh_key": body.ssh_key,
        "startup_script": body.startup_script,
        "disks": [],
        "reservation_id": reservation_id,
        "billing_type": billing_type,
    }
    project_instances[project_id][vm_id] = vm
    op = _create_operation(project_id, vm_id, "CREATE")

    def set_running():
        time.sleep(2.5)
        if vm_id in project_instances[project_id]:
            project_instances[project_id][vm_id]["state"] = "STATE_RUNNING"

    threading.Thread(target=set_running, daemon=True).start()
    return {"operation": op, "instance": vm}


# ==========================================
# Instance Lifecycle Operations
# ==========================================

class UpdateInstanceRequest(BaseModel):
    action: str  # "START" or "STOP"


@app.patch("/v1alpha5/projects/{project_id}/compute/vms/instances/{vm_id}")
def update_instance(project_id: str, vm_id: str, body: UpdateInstanceRequest, authorization: Optional[str] = Header(None)):
    """Stop or Start an instance. Stop releases reservation capacity. Start reclaims it."""
    check_auth(authorization)
    _check_project(project_id)

    if vm_id not in project_instances[project_id]:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": f"Instance {vm_id} not found"})

    vm = project_instances[project_id][vm_id]

    if body.action == "STOP":
        if vm["state"] != "STATE_RUNNING":
            raise HTTPException(400, detail={"code": "FAILED_PRECONDITION", "message": "Instance is not running"})
        vm["state"] = "STATE_STOPPING"
        op = _create_operation(project_id, vm_id, "STOP")

        def set_stopped():
            time.sleep(2)
            if vm_id in project_instances[project_id]:
                project_instances[project_id][vm_id]["state"] = "STATE_STOPPED"
                # Release reservation capacity on stop
                _release_reservation_capacity(project_id, vm)

        threading.Thread(target=set_stopped, daemon=True).start()

    elif body.action == "START":
        if vm["state"] != "STATE_STOPPED":
            raise HTTPException(400, detail={"code": "FAILED_PRECONDITION", "message": "Instance is not stopped"})

        # Reclaim reservation capacity on start
        if vm["reservation_id"]:
            rsv = project_reservations[project_id].get(vm["reservation_id"])
            if rsv and rsv["status"] == "ACTIVE":
                gpu_count = VM_TYPES[vm["type"]]["gpu_count"]
                if rsv["used_gpus"] + gpu_count <= rsv["total_gpus"]:
                    rsv["used_gpus"] += gpu_count
                else:
                    # Reservation full, fall back to on-demand
                    vm["reservation_id"] = None
                    vm["billing_type"] = "on_demand"

        vm["state"] = "STATE_STARTING"
        op = _create_operation(project_id, vm_id, "START")

        def set_running():
            time.sleep(2)
            if vm_id in project_instances[project_id]:
                project_instances[project_id][vm_id]["state"] = "STATE_RUNNING"

        threading.Thread(target=set_running, daemon=True).start()

    else:
        raise HTTPException(400, detail={"code": "INVALID_ARGUMENT", "message": f"Unknown action: {body.action}. Use START or STOP."})

    return {"operation": op, "instance": vm}


def _release_reservation_capacity(project_id: str, vm: dict):
    """Release GPU capacity back to reservation when VM stops or is deleted."""
    if vm.get("reservation_id"):
        rsv = project_reservations[project_id].get(vm["reservation_id"])
        if rsv:
            gpu_count = VM_TYPES[vm["type"]]["gpu_count"]
            rsv["used_gpus"] = max(0, rsv["used_gpus"] - gpu_count)


# --- Reboot: Graceful OS restart ---
# Sends ACPI signal. VM stays STATE_RUNNING throughout.
# OS handles shutdown/restart cleanly. Filesystem caches flushed.
# Takes ~30s to complete. Applications get SIGTERM.
@app.post("/v1alpha5/projects/{project_id}/compute/vms/instances/{vm_id}/reboot")
def reboot_instance(project_id: str, vm_id: str, authorization: Optional[str] = Header(None)):
    """
    Reboot: Graceful OS-level restart via ACPI signal.
    - VM state remains STATE_RUNNING throughout
    - OS performs clean shutdown sequence (SIGTERM to processes, flush caches)
    - Takes ~30 seconds
    - Equivalent to 'sudo reboot' inside the VM
    """
    check_auth(authorization)
    _check_project(project_id)

    if vm_id not in project_instances[project_id]:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": f"Instance {vm_id} not found"})

    vm = project_instances[project_id][vm_id]
    if vm["state"] != "STATE_RUNNING":
        raise HTTPException(400, detail={"code": "FAILED_PRECONDITION", "message": "Instance must be running to reboot"})

    # State stays RUNNING throughout — this is the key difference
    op = _create_operation(project_id, vm_id, "REBOOT")
    return {"operation": op, "instance": vm}


# --- Reset: Hard power cycle ---
# Like pulling the power cord. Immediate. No graceful shutdown.
# VM stays STATE_RUNNING. All volatile state (unflushed writes, memory) is LOST.
# Use when OS is unresponsive.
@app.post("/v1alpha5/projects/{project_id}/compute/vms/instances/{vm_id}/reset")
def reset_instance(project_id: str, vm_id: str, authorization: Optional[str] = Header(None)):
    """
    Reset: Hard power cycle (like pulling the power cord).
    - VM state remains STATE_RUNNING throughout
    - NO graceful shutdown — all volatile state is lost
    - Immediate effect, no waiting for OS
    - Same physical host, same IP, same disks
    - Use when VM is unresponsive or hung
    """
    check_auth(authorization)
    _check_project(project_id)

    if vm_id not in project_instances[project_id]:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": f"Instance {vm_id} not found"})

    vm = project_instances[project_id][vm_id]
    if vm["state"] != "STATE_RUNNING":
        raise HTTPException(400, detail={"code": "FAILED_PRECONDITION", "message": "Instance must be running to reset"})

    op = _create_operation(project_id, vm_id, "RESET")
    return {"operation": op, "instance": vm}


# --- Restart: Full stop + start cycle ---
# VM goes through full state transition: STOPPING → STOPPED → STARTING → RUNNING
# May be placed on different physical host. IP may change.
# Use when you want a clean environment or need to pick up host-level changes.
@app.post("/v1alpha5/projects/{project_id}/compute/vms/instances/{vm_id}/restart")
def restart_instance(project_id: str, vm_id: str, authorization: Optional[str] = Header(None)):
    """
    Restart: Full stop + start cycle.
    - VM transitions: STATE_RUNNING → STATE_STOPPING → STATE_STOPPED → STATE_STARTING → STATE_RUNNING
    - May be placed on different physical host
    - IP address may change
    - All volatile state is lost
    - Reservation capacity is released during stop, reclaimed during start
    - Takes ~10-15 seconds total
    """
    check_auth(authorization)
    _check_project(project_id)

    if vm_id not in project_instances[project_id]:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": f"Instance {vm_id} not found"})

    vm = project_instances[project_id][vm_id]
    if vm["state"] != "STATE_RUNNING":
        raise HTTPException(400, detail={"code": "FAILED_PRECONDITION", "message": "Instance must be running to restart"})

    vm["state"] = "STATE_STOPPING"
    op = _create_operation(project_id, vm_id, "RESTART")

    def do_restart():
        time.sleep(1)
        if vm_id not in project_instances[project_id]:
            return
        # Release reservation capacity during stop
        _release_reservation_capacity(project_id, vm)
        vm["state"] = "STATE_STOPPED"
        time.sleep(1)
        vm["state"] = "STATE_STARTING"
        # Reclaim reservation capacity during start
        if vm.get("reservation_id"):
            rsv = project_reservations[project_id].get(vm["reservation_id"])
            if rsv and rsv["status"] == "ACTIVE":
                gpu_count = VM_TYPES[vm["type"]]["gpu_count"]
                if rsv["used_gpus"] + gpu_count <= rsv["total_gpus"]:
                    rsv["used_gpus"] += gpu_count
        # May get new IP on restart
        vm["ip_address"] = f"203.0.113.{200 + hash(vm_id) % 50}"
        time.sleep(1)
        vm["state"] = "STATE_RUNNING"

    threading.Thread(target=do_restart, daemon=True).start()
    return {"operation": op, "instance": vm}


# ==========================================
# Delete Instance
# ==========================================

@app.delete("/v1alpha5/projects/{project_id}/compute/vms/instances/{vm_id}")
def delete_instance(project_id: str, vm_id: str, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    _check_project(project_id)

    if vm_id not in project_instances[project_id]:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": f"Instance {vm_id} not found"})

    vm = project_instances[project_id][vm_id]
    vm["state"] = "STATE_DELETING"
    op = _create_operation(project_id, vm_id, "DELETE")

    def do_delete():
        time.sleep(2)
        if vm_id in project_instances[project_id]:
            deleted = project_instances[project_id].pop(vm_id)
            # Release reservation capacity
            _release_reservation_capacity(project_id, deleted)
            # Return on-demand capacity
            if deleted["billing_type"] == "on_demand":
                loc = deleted["location"]
                vtype = deleted["type"]
                ON_DEMAND_CAPACITY[loc][vtype] = ON_DEMAND_CAPACITY.get(loc, {}).get(vtype, 0) + 1

    threading.Thread(target=do_delete, daemon=True).start()
    return {"operation": op}


# ==========================================
# Operations
# ==========================================

@app.get("/v1alpha5/projects/{project_id}/compute/vms/instances/operations")
def list_operations(
    project_id: str,
    authorization: Optional[str] = Header(None),
    resource_id: Optional[str] = Query(None),
    state: Optional[list[str]] = Query(None),
):
    check_auth(authorization)
    _check_project(project_id)
    ops = list(operations[project_id].values())
    if resource_id:
        ops = [o for o in ops if o["resource_id"] == resource_id]
    if state:
        ops = [o for o in ops if o["state"] in state]
    return {"items": ops}


@app.get("/v1alpha5/projects/{project_id}/compute/vms/instances/operations/{operation_id}")
def get_operation(project_id: str, operation_id: str, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    _check_project(project_id)
    if operation_id not in operations[project_id]:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": f"Operation {operation_id} not found"})
    return operations[project_id][operation_id]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
