# Crusoe Cloud API Reference

**Base URL**: `http://localhost:8001`
**Auth**: `Authorization: Bearer crusoe-test-key-001`
**Project ID**: `proj-001`

## Key Concepts

- All resources are **project-scoped**: every URL starts with `/v1alpha5/projects/{project_id}/...`
- Mutating operations (create, update, delete, reboot, reset, restart) are **asynchronous** and return an `operation` object
- Poll the operation endpoint to check completion (~2-3 seconds)
- **Reservations** provide pre-committed GPU capacity at discounted rates. VMs are auto-placed into reservations when available.

---

## Reservations

### List Reservations

```
GET /v1alpha5/projects/{project_id}/reservations
```

Query parameters:
- `status` — comma-separated filter, e.g. `ACTIVE,EXPIRED`

Response:
```json
{
  "items": [
    {
      "id": "rsv-001",
      "name": "8x a100-40gb @ $9.50/gpu/hr (rsv-001)",
      "status": "ACTIVE",
      "vm_type": "a100.8x",
      "gpu_type": "A100",
      "location": "us-west1",
      "total_gpus": 16,
      "used_gpus": 8,
      "unit_price_per_gpu_hour_usd": 9.50,
      "start_date": "2026-01-01T00:00:00Z",
      "end_date": "2026-12-31T23:59:59Z"
    }
  ]
}
```

Reservation statuses: `AWAITING_DELIVERY`, `ACTIVE`, `EXPIRED`

### Get Reservation

```
GET /v1alpha5/projects/{project_id}/reservations/{reservation_id}
```

### Capacity (On-Demand + Reserved)

```
GET /v1alpha5/projects/{project_id}/capacity
```

Response:
```json
{
  "items": [
    {
      "location": "us-west1",
      "vm_type": "h100.8x",
      "on_demand_available": 2,
      "reserved_available": 3,
      "total_available": 5
    }
  ]
}
```

---

## VM Types

```
GET /v1alpha5/projects/{project_id}/compute/vms/types
```

| Type | GPUs | GPU | vCPUs | RAM (GiB) | $/hr |
|------|------|-----|-------|-----------|------|
| `a100.1x` | 1 | A100 40GB | 12 | 85 | $1.48 |
| `a100.8x` | 8 | A100 40GB | 96 | 680 | $11.84 |
| `h100.1x` | 1 | H100 80GB | 16 | 128 | $3.46 |
| `h100.8x` | 8 | H100 80GB | 192 | 1440 | $27.68 |

---

## Instances

### List Instances

```
GET /v1alpha5/projects/{project_id}/compute/vms/instances
```

Query parameters: `states`, `types`, `locations`, `names`, `limit`

Response:
```json
{
  "items": [
    {
      "id": "uuid",
      "name": "gpu-worker-1",
      "type": "a100.8x",
      "location": "us-west1",
      "state": "STATE_RUNNING",
      "ip_address": "203.0.113.10",
      "private_ip_address": "10.100.0.10",
      "created_at": "2026-03-15T10:00:00Z",
      "ssh_key": "default-key",
      "reservation_id": "rsv-001",
      "billing_type": "reserved"
    }
  ]
}
```

- `billing_type`: `"reserved"` or `"on_demand"`
- `reservation_id`: null for on-demand instances

### Get Instance

```
GET /v1alpha5/projects/{project_id}/compute/vms/instances/{vm_id}
```

### Create Instance

```
POST /v1alpha5/projects/{project_id}/compute/vms/instances
```

Body:
```json
{
  "name": "my-gpu-node",
  "type": "h100.8x",
  "location": "us-west1",
  "ssh_key": "my-key",
  "reservation_id": "rsv-002"
}
```

- `reservation_id` is optional. If omitted, the system auto-places into the cheapest matching reservation. If no reservation has space, falls back to on-demand.
- If `reservation_id` is specified but unavailable, returns `FAILED_PRECONDITION` error.

Response (async):
```json
{
  "operation": {"operation_id": "op-uuid", "state": "IN_PROGRESS", "action": "CREATE"},
  "instance": {"id": "vm-uuid", "state": "STATE_CREATING", "billing_type": "reserved", "reservation_id": "rsv-002"}
}
```

### Stop / Start

```
PATCH /v1alpha5/projects/{project_id}/compute/vms/instances/{vm_id}
```

Body:
```json
{"action": "STOP"}
```

**Important**: Stopping a VM **releases** its reservation capacity. Starting reclaims it (if still available).

### Delete Instance

```
DELETE /v1alpha5/projects/{project_id}/compute/vms/instances/{vm_id}
```

---

## Reboot vs Reset vs Restart

Three distinct operations with different semantics:

### Reboot (Graceful OS restart)

```
POST /v1alpha5/projects/{project_id}/compute/vms/instances/{vm_id}/reboot
```

- Sends **ACPI signal** to the OS
- OS performs **clean shutdown** (SIGTERM to processes, flush disk caches)
- VM state **stays `STATE_RUNNING`** throughout
- **Same host, same IP, same disks**
- Takes ~30 seconds
- Equivalent to `sudo reboot` inside the VM

### Reset (Hard power cycle)

```
POST /v1alpha5/projects/{project_id}/compute/vms/instances/{vm_id}/reset
```

- **Immediate** power cycle — like pulling the power cord
- **No graceful shutdown** — all volatile state (unflushed writes, memory) is LOST
- VM state **stays `STATE_RUNNING`** throughout
- **Same host, same IP, same disks**
- Use when the VM is **unresponsive** or the OS is hung

### Restart (Full stop + start cycle)

```
POST /v1alpha5/projects/{project_id}/compute/vms/instances/{vm_id}/restart
```

- Full lifecycle: `STATE_RUNNING → STATE_STOPPING → STATE_STOPPED → STATE_STARTING → STATE_RUNNING`
- **May be placed on different physical host**
- **IP address may change**
- All volatile state is lost
- **Reservation capacity is released during stop, reclaimed during start**
- Takes ~10-15 seconds total

| | Reboot | Reset | Restart |
|---|--------|-------|---------|
| Graceful | ✅ Yes | ❌ No | ❌ No |
| State change | None | None | STOPPING→STOPPED→STARTING→RUNNING |
| Same host | ✅ Yes | ✅ Yes | ❓ Maybe |
| IP changes | ❌ No | ❌ No | ⚠️ Possible |
| Reservation released | ❌ No | ❌ No | ✅ Yes (temporarily) |
| Use case | Routine restart | Unresponsive VM | Clean environment |

---

## Operations

### Poll Operation

```
GET /v1alpha5/projects/{project_id}/compute/vms/instances/operations/{operation_id}
```

```json
{
  "operation_id": "op-uuid",
  "state": "SUCCEEDED",
  "action": "CREATE",
  "resource_id": "vm-uuid"
}
```

Operation states: `IN_PROGRESS`, `SUCCEEDED`
Actions: `CREATE`, `START`, `STOP`, `DELETE`, `REBOOT`, `RESET`, `RESTART`

---

## Locations

`us-east1`, `us-west1`, `eu-west1`

## Instance States

`STATE_CREATING`, `STATE_RUNNING`, `STATE_STOPPING`, `STATE_STOPPED`, `STATE_STARTING`, `STATE_DELETING`

## Error Format

```json
{"code": "ERROR_CODE", "message": "Description"}
```

Codes: `UNAUTHENTICATED`, `PERMISSION_DENIED`, `NOT_FOUND`, `INVALID_ARGUMENT`, `RESOURCE_EXHAUSTED`, `FAILED_PRECONDITION`
