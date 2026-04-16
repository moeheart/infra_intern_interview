# GPU Cloud VM Manager - Interview Challenge

## Background

You work at a company that uses GPU VMs from three cloud providers:
- **Crusoe Cloud** — REST API, project-scoped, async operations, reservations, reboot/reset/restart
- **Lambda Cloud** — REST API, flat/simple, synchronous, reserved instances
- **Nebius AI Cloud** — **gRPC** interface, parent-scoped, async operations, reservation policies

Each provider has a different API design and protocol. Your job is to build a **unified CLI tool** that abstracts away these differences.

## Time: 1 - 1.5 hours

You may use any programming language, libraries, AI tools, and internet resources.

---

## Layer 1: Unified CLI (Target: ~30-40 min)

Build a CLI that provides a unified interface to manage VMs across all three providers.

### Required Commands

```bash
# List all instances across all providers (or filter by provider)
vm list [--provider <name>]

# Create a new instance
vm create --provider <name> --gpu <type> --count <n> [--name <name>] [--region <region>]

# Get instance details
vm get <instance_id> --provider <name>

# Stop an instance
vm stop <instance_id> --provider <name>

# Start an instance
vm start <instance_id> --provider <name>

# Destroy/terminate an instance
vm destroy <instance_id> --provider <name>
```

### What we're looking for

- Clean abstraction over three different API styles (two REST + one **gRPC**)
- Proper error handling (auth failures, not found, capacity errors)
- Readable output (table, JSON, or similar)
- Code that's easy to extend to new providers

---

## Layer 2: Fleet Manager (Target: ~40-50 min)

Build on top of Layer 1 to support **fleet operations** — managing groups of VMs as a logical unit.

### Required Commands

```bash
# Request N machines of a given GPU type, spread across providers
vm fleet create --gpu <type> --count <n> [--name <fleet_name>]

# List all fleets
vm fleet list

# Show fleet status (which VMs, which providers, states)
vm fleet status <fleet_name>

# Destroy an entire fleet
vm fleet destroy <fleet_name>
```

### Key Challenges

1. **Cross-provider scheduling**: Query all providers for capacity, then allocate across them to fulfill the request.
2. **Partial failure handling**: If you need 8 VMs and provider A only has 3, get the rest from provider B/C.
3. **Rollback**: If the total request can't be fulfilled, clean up any VMs that were already created.
4. **Fleet state tracking**: Persist which VMs belong to which fleet (file, SQLite, etc.).
5. **Concurrent operations**: Don't create VMs one at a time — parallelize across providers.

---

## Mock Servers

Three mock servers simulate the real cloud APIs. Start them with:

```bash
cd mock_servers
bash start_all.sh
```

| Provider | Endpoint | API Key | Protocol |
|----------|----------|---------|----------|
| Crusoe | `http://localhost:8001` | `crusoe-test-key-001` | REST (project: `proj-001`) |
| Lambda | `http://localhost:8002` | `lambda-test-key-001` | REST |
| Nebius | `grpc://localhost:50051` | `nebius-test-key-001` | **gRPC** (parent: `project-e1a2b3c4`) |

### Quick test

```bash
# Lambda - list instances (REST)
curl -s http://localhost:8002/api/v1/instances \
  -H "Authorization: Bearer lambda-test-key-001" | python3 -m json.tool

# Crusoe - list instances (REST)
curl -s http://localhost:8001/v1alpha5/projects/proj-001/compute/vms/instances \
  -H "Authorization: Bearer crusoe-test-key-001" | python3 -m json.tool

# Nebius - list instances (gRPC — requires grpcurl or a gRPC client)
grpcurl -plaintext \
  -H "authorization: Bearer nebius-test-key-001" \
  -d '{"parent_id": "project-e1a2b3c4"}' \
  localhost:50051 nebius.compute.v1.InstanceService/List
```

See `docs/` for detailed API documentation per provider.

### Key API Differences

| | Crusoe | Lambda | Nebius |
|---|--------|--------|--------|
| **Protocol** | REST | REST | **gRPC** |
| **Scoping** | `/projects/{pid}/...` | Flat `/api/v1/...` | `parent_id` field |
| **Create** | POST → async operation | POST launch → sync IDs | RPC Create → async operation |
| **Stop/Start** | PATCH + action body | No stop/start | RPC Stop / Start |
| **Delete** | DELETE → async | POST terminate (batch) | RPC Delete → async |
| **GPU naming** | `h100.8x` | `gpu_8x_h100` | `gpu-h100-sxm` + preset |
| **Reservations** | Auto-placement, explicit ID | `is_reserved` flag, `reservation_id` | Policy: AUTO/FORBID/STRICT |
| **Reboot/Reset** | 3 distinct ops (see below) | Restart only | — |

### Crusoe: Reboot vs Reset vs Restart

| | Reboot | Reset | Restart |
|---|--------|-------|---------|
| **Method** | Graceful (ACPI) | Hard (power cycle) | Full stop+start |
| **State change** | Stays RUNNING | Stays RUNNING | STOPPING→STOPPED→STARTING→RUNNING |
| **Same host/IP** | Yes | Yes | Maybe not |
| **Use case** | Routine restart | Unresponsive VM | Clean environment |

### Reservations

Each provider handles reservations differently:

- **Crusoe**: VMs auto-placed into cheapest matching reservation. Explicit `reservation_id` optional. Stop releases capacity, start reclaims it.
- **Lambda**: Instances flagged `is_reserved: true`. Reserved instances **cannot be terminated** via API. Launch with `reservation_id` to use reserved capacity.
- **Nebius**: `ReservationPolicy` in instance spec — `AUTO` (try reservation first), `FORBID` (always on-demand), `STRICT` (must use specific reservation, fail otherwise).

---

## Evaluation Criteria

| Area | What we look at |
|------|-----------------|
| **Abstraction design** | How cleanly do you unify 3 different APIs + protocols (REST + gRPC)? |
| **Error handling** | Capacity errors, partial failures, auth issues, reserved instance constraints. |
| **Concurrency** | Parallel VM creation in fleet mode. Not sequential one-by-one. |
| **State management** | Fleet tracking, reconciliation with real provider state. |
| **Code quality** | Readable, testable, well-structured. Not over-engineered. |
| **Trade-off communication** | Can you explain your design decisions? |

---

## Tips

- Start with Layer 1. Get `list` and `create` working for one REST provider first, then add gRPC.
- For Layer 2, think about what happens when things go wrong mid-flight.
- The mock servers have **limited capacity** — this is intentional.
- Crusoe and Nebius operations are async (2-3 second delay). Lambda is synchronous.
- Reserved instances on Lambda can't be terminated — your fleet manager needs to handle this.
- The Nebius proto files are in `mock_servers/proto/` — you'll need to compile them for your language.
- Don't over-engineer Layer 1 — you need time for Layer 2.



## Test

python3 candidate/vm.py list
python3 candidate/vm.py list --provider lambda
python3 candidate/vm.py create --provider crusoe --gpu h100.1x --count 1 --region us-west
python3 candidate/vm.py get <instance_id> --provider crusoe
python3 candidate/vm.py destroy <instance_id> --provider lambda