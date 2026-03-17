# Nebius AI Cloud API Reference

**Endpoint**: `grpc://localhost:50051`
**Auth**: metadata key `authorization` with value `Bearer nebius-test-key-001`
**Parent (Project) ID**: `project-e1a2b3c4`

## Key Concepts

- Nebius uses **native gRPC** (not REST)
- You need a **gRPC client** (e.g. `grpcurl`, Python `grpcio`, Go `google.golang.org/grpc`)
- Resources are **parent-scoped** (similar to GCP's resource hierarchy)
- Each resource has `metadata`, `spec` (desired state), `status` (observed state)
- Mutating operations return a long-running `Operation`. Poll until `done: true`.
- **ReservationPolicy** controls how instances use reserved capacity: `AUTO`, `FORBID`, or `STRICT`

## Proto Files

Proto definitions are in `mock_servers/proto/nebius/compute/v1/`:
- `instance.proto` — Instance, Reservation, and related message types
- `instance_service.proto` — InstanceService and ReservationService RPC definitions

Compile with:
```bash
python -m grpc_tools.protoc \
  --proto_path=proto \
  --python_out=generated \
  --grpc_python_out=generated \
  nebius/compute/v1/instance.proto \
  nebius/compute/v1/instance_service.proto
```

## Server Reflection

The server supports **gRPC reflection**. You can discover services with:
```bash
grpcurl -plaintext localhost:50051 list
grpcurl -plaintext localhost:50051 describe nebius.compute.v1.InstanceService
```

---

## Services

### InstanceService

| RPC | Request | Response | Description |
|-----|---------|----------|-------------|
| `Get` | `GetInstanceRequest` | `Instance` | Get instance by ID |
| `List` | `ListInstancesRequest` | `ListInstancesResponse` | List instances under a parent |
| `Create` | `CreateInstanceRequest` | `Operation` | Create instance (async) |
| `Delete` | `DeleteInstanceRequest` | `Operation` | Delete instance (async) |
| `Start` | `StartInstanceRequest` | `Operation` | Start stopped instance |
| `Stop` | `StopInstanceRequest` | `Operation` | Stop running instance |

### ReservationService

| RPC | Request | Response | Description |
|-----|---------|----------|-------------|
| `Get` | `GetReservationRequest` | `Reservation` | Get reservation by ID |
| `List` | `ListReservationsRequest` | `ListReservationsResponse` | List reservations under a parent |

---

## Examples (grpcurl)

### List Instances
```bash
grpcurl -plaintext \
  -H "authorization: Bearer nebius-test-key-001" \
  -d '{"parent_id": "project-e1a2b3c4"}' \
  localhost:50051 nebius.compute.v1.InstanceService/List
```

### Create Instance (AUTO reservation)
```bash
grpcurl -plaintext \
  -H "authorization: Bearer nebius-test-key-001" \
  -d '{
    "metadata": {"parent_id": "project-e1a2b3c4", "name": "my-node", "labels": {"team": "ml"}},
    "spec": {
      "resources": {"platform": "gpu-h100-sxm", "preset": "8gpu-160vcpu-1600gb"},
      "reservation_policy": {"policy": 0}
    }
  }' \
  localhost:50051 nebius.compute.v1.InstanceService/Create
```

### Create Instance (STRICT reservation)
```bash
grpcurl -plaintext \
  -H "authorization: Bearer nebius-test-key-001" \
  -d '{
    "metadata": {"parent_id": "project-e1a2b3c4", "name": "strict-node"},
    "spec": {
      "resources": {"platform": "gpu-h200-sxm", "preset": "8gpu-160vcpu-2048gb"},
      "reservation_policy": {"policy": 2, "reservation_ids": ["rsv-neb-002"]}
    }
  }' \
  localhost:50051 nebius.compute.v1.InstanceService/Create
```

### Create Instance (FORBID reservation — on-demand only)
```bash
grpcurl -plaintext \
  -H "authorization: Bearer nebius-test-key-001" \
  -d '{
    "metadata": {"parent_id": "project-e1a2b3c4", "name": "ondemand-node"},
    "spec": {
      "resources": {"platform": "gpu-h100-sxm", "preset": "1gpu-16vcpu-200gb"},
      "reservation_policy": {"policy": 1}
    }
  }' \
  localhost:50051 nebius.compute.v1.InstanceService/Create
```

### Stop / Start / Delete
```bash
grpcurl -plaintext \
  -H "authorization: Bearer nebius-test-key-001" \
  -d '{"id": "INSTANCE_ID"}' \
  localhost:50051 nebius.compute.v1.InstanceService/Stop

grpcurl -plaintext \
  -H "authorization: Bearer nebius-test-key-001" \
  -d '{"id": "INSTANCE_ID"}' \
  localhost:50051 nebius.compute.v1.InstanceService/Start
```

### List Reservations
```bash
grpcurl -plaintext \
  -H "authorization: Bearer nebius-test-key-001" \
  -d '{"parent_id": "project-e1a2b3c4"}' \
  localhost:50051 nebius.compute.v1.ReservationService/List
```

---

## Examples (Python)

```python
import grpc
import sys
sys.path.insert(0, "generated")
from nebius.compute.v1 import instance_pb2, instance_service_pb2, instance_service_pb2_grpc

channel = grpc.insecure_channel("localhost:50051")
stub = instance_service_pb2_grpc.InstanceServiceStub(channel)
meta = [("authorization", "Bearer nebius-test-key-001")]

# List
resp = stub.List(
    instance_service_pb2.ListInstancesRequest(parent_id="project-e1a2b3c4"),
    metadata=meta,
)
for inst in resp.instances:
    print(inst.metadata.name, inst.status.state, inst.status.reservation_id)

# Create with AUTO reservation policy
op = stub.Create(
    instance_service_pb2.CreateInstanceRequest(
        metadata=instance_pb2.ResourceMetadata(
            parent_id="project-e1a2b3c4", name="my-node", labels={"team": "ml"},
        ),
        spec=instance_pb2.InstanceSpec(
            resources=instance_pb2.ResourcesSpec(platform="gpu-h100-sxm", preset="8gpu-160vcpu-1600gb"),
            reservation_policy=instance_pb2.ReservationPolicy(policy=0),  # AUTO
        ),
    ),
    metadata=meta,
)
print(op.id, op.resource_id, op.done)
```

---

## Platforms & Presets

| Platform | Preset | GPUs | GPU | vCPUs | RAM (GiB) |
|----------|--------|------|-----|-------|-----------|
| `gpu-h100-sxm` | `1gpu-16vcpu-200gb` | 1 | H100 | 16 | 200 |
| `gpu-h100-sxm` | `8gpu-160vcpu-1600gb` | 8 | H100 | 160 | 1600 |
| `gpu-h200-sxm` | `1gpu-20vcpu-256gb` | 1 | H200 | 20 | 256 |
| `gpu-h200-sxm` | `8gpu-160vcpu-2048gb` | 8 | H200 | 160 | 2048 |

## Instance States

| Value | Name |
|-------|------|
| 0 | STATE_UNSPECIFIED |
| 1 | CREATING |
| 2 | RUNNING |
| 3 | STOPPING |
| 4 | STOPPED |
| 5 | STARTING |
| 6 | DELETING |
| 7 | ERROR |

## Reservation Policy

| Value | Name | Behavior |
|-------|------|----------|
| 0 | AUTO | Auto-place in matching reservation, fall back to on-demand |
| 1 | FORBID | Never use reservation, always on-demand |
| 2 | STRICT | Must use one of the specified `reservation_ids`, fail if unavailable |

## Reservation States

| Value | Name |
|-------|------|
| 0 | RESERVATION_STATE_UNSPECIFIED |
| 1 | RESERVATION_ACTIVE |
| 2 | RESERVATION_EXPIRED |
| 3 | RESERVATION_PENDING |

## Error Codes (gRPC)

| Code | Name | Meaning |
|------|------|---------|
| 3 | INVALID_ARGUMENT | Bad request parameters |
| 5 | NOT_FOUND | Resource doesn't exist |
| 7 | PERMISSION_DENIED | Wrong parent_id |
| 8 | RESOURCE_EXHAUSTED | No capacity |
| 9 | FAILED_PRECONDITION | Wrong state (e.g. stop a non-running instance) |
| 16 | UNAUTHENTICATED | Bad or missing API key |
