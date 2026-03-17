"""
Lambda Cloud API Mock Server (v1)
Port: 8002

Follows: https://docs.lambda.ai/api/cloud/reference
Key traits:
  - Flat/simple REST API, no project scoping
  - Synchronous operations (launch/terminate return immediately)
  - Instances have is_reserved flag
  - Reserved instances cannot be terminated via API
"""

import uuid
import time
from typing import Optional
from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(title="Lambda Cloud API Mock", version="1.9.3")

# --- Auth ---
VALID_API_KEYS = {"lambda-test-key-001", "lambda-test-key-002"}


def check_auth(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(401, detail={
            "error": {"code": "global/invalid-api-key", "message": "API key was invalid, expired, or deleted.",
                      "suggestion": "Check your API key or create a new one, then try again."}
        })
    token = authorization.replace("Bearer ", "").replace("Basic ", "")
    if token not in VALID_API_KEYS:
        raise HTTPException(401, detail={
            "error": {"code": "global/invalid-api-key", "message": "API key was invalid, expired, or deleted.",
                      "suggestion": "Check your API key or create a new one, then try again."}
        })


# --- Data Models ---
INSTANCE_TYPES = {
    "gpu_8x_a100": {
        "name": "gpu_8x_a100",
        "description": "8x A100 (40 GB SXM4)",
        "gpu_description": "A100 (40 GB SXM4)",
        "price_cents_per_hour": 1184,
        "specs": {"vcpus": 124, "memory_gib": 1800, "storage_gib": 6144, "gpus": 8},
    },
    "gpu_1x_a100": {
        "name": "gpu_1x_a100",
        "description": "1x A100 (40 GB SXM4)",
        "gpu_description": "A100 (40 GB SXM4)",
        "price_cents_per_hour": 148,
        "specs": {"vcpus": 16, "memory_gib": 225, "storage_gib": 768, "gpus": 1},
    },
    "gpu_8x_h100": {
        "name": "gpu_8x_h100",
        "description": "8x H100 (80 GB SXM5)",
        "gpu_description": "H100 (80 GB SXM5)",
        "price_cents_per_hour": 2768,
        "specs": {"vcpus": 208, "memory_gib": 1800, "storage_gib": 24780, "gpus": 8},
    },
    "gpu_1x_h100": {
        "name": "gpu_1x_h100",
        "description": "1x H100 (80 GB SXM5)",
        "gpu_description": "H100 (80 GB SXM5)",
        "price_cents_per_hour": 346,
        "specs": {"vcpus": 26, "memory_gib": 225, "storage_gib": 3096, "gpus": 1},
    },
}

REGIONS = {
    "us-west-1": {"name": "us-west-1", "description": "California, USA"},
    "us-east-1": {"name": "us-east-1", "description": "Virginia, USA"},
    "eu-west-1": {"name": "eu-west-1", "description": "London, UK"},
}

# On-demand capacity: {region: {instance_type: available_count}}
CAPACITY = {
    "us-west-1": {"gpu_8x_a100": 3, "gpu_1x_a100": 10, "gpu_8x_h100": 2, "gpu_1x_h100": 5},
    "us-east-1": {"gpu_8x_a100": 0, "gpu_1x_a100": 5, "gpu_8x_h100": 1, "gpu_1x_h100": 8},
    "eu-west-1": {"gpu_8x_a100": 1, "gpu_1x_a100": 3, "gpu_8x_h100": 0, "gpu_1x_h100": 2},
}

# Reservations: pre-configured reserved capacity blocks
reservations: dict[str, dict] = {}

# In-memory instance store
instances: dict[str, dict] = {}


def _make_instance(iid: str, name: str, region: str, itype: str, ssh_keys: list[str],
                   is_reserved: bool = False, reservation_id: str = None) -> dict:
    return {
        "id": iid,
        "name": name,
        "ip": f"198.51.100.{10 + len(instances)}",
        "private_ip": f"10.0.2.{10 + len(instances)}",
        "status": "active",
        "ssh_key_names": ssh_keys,
        "file_system_names": [],
        "region": REGIONS[region],
        "instance_type": INSTANCE_TYPES[itype],
        "hostname": name or iid[:8],
        "jupyter_token": uuid.uuid4().hex,
        "jupyter_url": f"https://jupyter-{uuid.uuid4().hex[:16]}.lambdaspaces.com",
        "is_reserved": is_reserved,
        "reservation_id": reservation_id,
        "actions": {
            "migrate": {"available": True},
            "rebuild": {"available": True},
            "restart": {"available": True},
            "cold_reboot": {"available": True},
            "terminate": {"available": not is_reserved},  # Can't terminate reserved instances
        },
        "tags": [],
    }


def _seed():
    # On-demand instances
    iid1 = uuid.uuid4().hex
    instances[iid1] = _make_instance(iid1, "training-node-1", "us-west-1", "gpu_8x_a100", ["default-key"])

    iid2 = uuid.uuid4().hex
    instances[iid2] = _make_instance(iid2, "dev-box", "us-east-1", "gpu_1x_h100", ["default-key"])

    # Reserved instances
    rsv_id = "rsv-lambda-001"
    reservations[rsv_id] = {
        "id": rsv_id,
        "instance_type": "gpu_8x_h100",
        "region": "us-west-1",
        "quantity": 2,
        "used": 1,
        "weeks_remaining": 38,
        "start_date": "2026-01-15",
        "end_date": "2026-12-31",
    }

    iid3 = uuid.uuid4().hex
    instances[iid3] = _make_instance(
        iid3, "reserved-h100-node", "us-west-1", "gpu_8x_h100",
        ["default-key"], is_reserved=True, reservation_id=rsv_id,
    )

    rsv_id2 = "rsv-lambda-002"
    reservations[rsv_id2] = {
        "id": rsv_id2,
        "instance_type": "gpu_1x_a100",
        "region": "us-east-1",
        "quantity": 4,
        "used": 0,
        "weeks_remaining": 20,
        "start_date": "2026-02-01",
        "end_date": "2026-09-30",
    }

_seed()


# --- Endpoints ---

@app.get("/api/v1/instances")
def list_instances(authorization: Optional[str] = Header(None), cluster_id: Optional[str] = Query(None)):
    check_auth(authorization)
    return {"data": list(instances.values())}


@app.get("/api/v1/instances/{instance_id}")
def get_instance(instance_id: str, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    if instance_id not in instances:
        raise HTTPException(404, detail={
            "error": {"code": "global/object-does-not-exist", "message": "Specified instance does not exist."}
        })
    return {"data": instances[instance_id]}


@app.post("/api/v1/instances/{instance_id}")
def update_instance(instance_id: str, body: dict, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    if instance_id not in instances:
        raise HTTPException(404, detail={
            "error": {"code": "global/object-does-not-exist", "message": "Specified instance does not exist."}
        })
    if "name" in body:
        instances[instance_id]["name"] = body["name"]
    return {"data": instances[instance_id]}


@app.get("/api/v1/instance-types")
def list_instance_types(authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    result = {}
    for type_name, type_info in INSTANCE_TYPES.items():
        regions_available = []
        for region_name, region_capacity in CAPACITY.items():
            if region_capacity.get(type_name, 0) > 0:
                regions_available.append(REGIONS[region_name])
        result[type_name] = {
            "instance_type": type_info,
            "regions_with_capacity_available": regions_available,
        }
    return {"data": result}


# --- Reservations ---

@app.get("/api/v1/reservations")
def list_reservations(authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    return {"data": list(reservations.values())}


@app.get("/api/v1/reservations/{reservation_id}")
def get_reservation(reservation_id: str, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    if reservation_id not in reservations:
        raise HTTPException(404, detail={
            "error": {"code": "global/object-does-not-exist", "message": "Specified reservation does not exist."}
        })
    return {"data": reservations[reservation_id]}


# --- Launch ---

class LaunchRequest(BaseModel):
    region_name: str
    instance_type_name: str
    ssh_key_names: list[str]
    name: Optional[str] = None
    hostname: Optional[str] = None
    quantity: int = 1
    reservation_id: Optional[str] = None   # Launch into specific reservation


@app.post("/api/v1/instance-operations/launch")
def launch_instance(body: LaunchRequest, authorization: Optional[str] = Header(None)):
    check_auth(authorization)

    if body.instance_type_name not in INSTANCE_TYPES:
        raise HTTPException(400, detail={
            "error": {"code": "global/invalid-parameters", "message": f"Unknown instance type: {body.instance_type_name}"}
        })
    if body.region_name not in REGIONS:
        raise HTTPException(400, detail={
            "error": {"code": "global/invalid-parameters", "message": f"Unknown region: {body.region_name}"}
        })

    is_reserved = False
    reservation_id = None

    if body.reservation_id:
        # Launch as reserved instance
        rsv = reservations.get(body.reservation_id)
        if not rsv:
            raise HTTPException(404, detail={
                "error": {"code": "global/object-does-not-exist",
                          "message": f"Reservation {body.reservation_id} does not exist."}
            })
        if rsv["instance_type"] != body.instance_type_name:
            raise HTTPException(400, detail={
                "error": {"code": "global/invalid-parameters",
                          "message": f"Reservation is for {rsv['instance_type']}, not {body.instance_type_name}."}
            })
        if rsv["region"] != body.region_name:
            raise HTTPException(400, detail={
                "error": {"code": "global/invalid-parameters",
                          "message": f"Reservation is in {rsv['region']}, not {body.region_name}."}
            })
        remaining = rsv["quantity"] - rsv["used"]
        if remaining < body.quantity:
            raise HTTPException(400, detail={
                "error": {"code": "instance-operations/launch/insufficient-capacity",
                          "message": f"Reservation has {remaining} slots, requested {body.quantity}.",
                          "suggestion": "Reduce quantity or use on-demand."}
            })
        rsv["used"] += body.quantity
        is_reserved = True
        reservation_id = body.reservation_id
    else:
        # On-demand launch
        available = CAPACITY.get(body.region_name, {}).get(body.instance_type_name, 0)
        if available < body.quantity:
            raise HTTPException(400, detail={
                "error": {"code": "instance-operations/launch/insufficient-capacity",
                          "message": f"Insufficient capacity. Requested {body.quantity}, available {available}.",
                          "suggestion": "Try a different region or instance type."}
            })
        CAPACITY[body.region_name][body.instance_type_name] -= body.quantity

    launched_ids = []
    for i in range(body.quantity):
        iid = uuid.uuid4().hex
        name = f"{body.name or body.instance_type_name}-{i}" if body.quantity > 1 else (body.name or body.instance_type_name)
        instances[iid] = _make_instance(
            iid, name, body.region_name, body.instance_type_name,
            body.ssh_key_names, is_reserved=is_reserved, reservation_id=reservation_id,
        )
        launched_ids.append(iid)

    return {"data": {"instance_ids": launched_ids}}


# --- Terminate ---

class TerminateRequest(BaseModel):
    instance_ids: list[str]


@app.post("/api/v1/instance-operations/terminate")
def terminate_instance(body: TerminateRequest, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    terminated = []
    for iid in body.instance_ids:
        if iid not in instances:
            raise HTTPException(404, detail={
                "error": {"code": "global/object-does-not-exist", "message": f"Instance {iid} does not exist."}
            })
        inst = instances[iid]

        # Cannot terminate reserved instances
        if inst.get("is_reserved"):
            raise HTTPException(400, detail={
                "error": {"code": "instance-operations/terminate/reserved-instance",
                          "message": f"Instance {iid} is a reserved instance and cannot be terminated via API.",
                          "suggestion": "Contact support to manage reserved instances."}
            })

        instances.pop(iid)
        # Return on-demand capacity
        region = inst["region"]["name"]
        itype = inst["instance_type"]["name"]
        CAPACITY[region][itype] = CAPACITY.get(region, {}).get(itype, 0) + 1
        inst["status"] = "terminated"
        terminated.append(inst)

    return {"data": {"terminated_instances": terminated}}


# --- Restart ---

class RestartRequest(BaseModel):
    instance_ids: list[str]


@app.post("/api/v1/instance-operations/restart")
def restart_instance(body: RestartRequest, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    restarted = []
    for iid in body.instance_ids:
        if iid not in instances:
            raise HTTPException(404, detail={
                "error": {"code": "global/object-does-not-exist", "message": f"Instance {iid} does not exist."}
            })
        instances[iid]["status"] = "active"
        restarted.append(instances[iid])
    return {"data": {"restarted_instances": restarted}}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
