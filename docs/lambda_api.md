# Lambda Cloud API Reference

**Base URL**: `http://localhost:8002`
**Auth**: `Authorization: Bearer lambda-test-key-001`

## Key Concepts

- **Flat, simple REST API** — no project scoping
- **Synchronous** operations — launch/terminate return immediately
- Instances have an `is_reserved` flag — reserved instances **cannot be terminated** via API
- Reservations represent pre-committed capacity blocks

---

## Reservations

### List Reservations

```
GET /api/v1/reservations
```

Response:
```json
{
  "data": [
    {
      "id": "rsv-lambda-001",
      "instance_type": "gpu_8x_h100",
      "region": "us-west-1",
      "quantity": 2,
      "used": 1,
      "weeks_remaining": 38,
      "start_date": "2026-01-15",
      "end_date": "2026-12-31"
    }
  ]
}
```

### Get Reservation

```
GET /api/v1/reservations/{reservation_id}
```

---

## Instances

### List Instances

```
GET /api/v1/instances
```

Response:
```json
{
  "data": [
    {
      "id": "0920582c7ff041399e34823a0be62549",
      "name": "My Instance",
      "ip": "198.51.100.2",
      "private_ip": "10.0.2.100",
      "status": "active",
      "ssh_key_names": ["my-key"],
      "region": {"name": "us-west-1", "description": "California, USA"},
      "instance_type": {
        "name": "gpu_8x_a100",
        "description": "8x A100 (40 GB SXM4)",
        "gpu_description": "A100 (40 GB SXM4)",
        "price_cents_per_hour": 1184,
        "specs": {"vcpus": 124, "memory_gib": 1800, "storage_gib": 6144, "gpus": 8}
      },
      "is_reserved": false,
      "reservation_id": null,
      "actions": {
        "terminate": {"available": true},
        "restart": {"available": true}
      }
    }
  ]
}
```

**Note**: For reserved instances, `is_reserved: true` and `actions.terminate.available: false`.

### Get Instance

```
GET /api/v1/instances/{id}
```

### List Instance Types (with availability)

```
GET /api/v1/instance-types
```

Shows on-demand capacity per region.

### Launch Instance

```
POST /api/v1/instance-operations/launch
```

Body:
```json
{
  "region_name": "us-west-1",
  "instance_type_name": "gpu_8x_a100",
  "ssh_key_names": ["my-key"],
  "name": "my-training-node",
  "quantity": 1,
  "reservation_id": "rsv-lambda-001"
}
```

- `reservation_id` is optional. If provided, launches into that reservation.
- If omitted, launches as on-demand.

Response:
```json
{"data": {"instance_ids": ["abc123..."]}}
```

**Capacity error:**
```json
{
  "error": {
    "code": "instance-operations/launch/insufficient-capacity",
    "message": "Insufficient capacity. Requested 5, available 2.",
    "suggestion": "Try a different region or instance type."
  }
}
```

### Terminate Instance

```
POST /api/v1/instance-operations/terminate
```

Body:
```json
{"instance_ids": ["abc123..."]}
```

**Cannot terminate reserved instances:**
```json
{
  "error": {
    "code": "instance-operations/terminate/reserved-instance",
    "message": "Instance abc123 is a reserved instance and cannot be terminated via API.",
    "suggestion": "Contact support to manage reserved instances."
  }
}
```

### Restart Instance

```
POST /api/v1/instance-operations/restart
```

Body:
```json
{"instance_ids": ["abc123..."]}
```

---

## Instance Types

| Name | GPUs | GPU Type | vCPUs | RAM (GiB) | $/hr |
|------|------|----------|-------|-----------|------|
| `gpu_1x_a100` | 1 | A100 40GB | 16 | 225 | $1.48 |
| `gpu_8x_a100` | 8 | A100 40GB | 124 | 1800 | $11.84 |
| `gpu_1x_h100` | 1 | H100 80GB | 26 | 225 | $3.46 |
| `gpu_8x_h100` | 8 | H100 80GB | 208 | 1800 | $27.68 |

## Regions

| Code | Description |
|------|-------------|
| `us-west-1` | California, USA |
| `us-east-1` | Virginia, USA |
| `eu-west-1` | London, UK |

## Instance Statuses

`booting`, `active`, `unhealthy`, `terminated`, `terminating`

## Error Format

```json
{"error": {"code": "error/code", "message": "Description", "suggestion": "How to fix"}}
```
