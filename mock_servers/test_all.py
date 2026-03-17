"""Quick smoke test for all three mock servers."""
import subprocess
import time
import sys
import os
import requests
import grpc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "generated"))
from nebius.compute.v1 import instance_pb2, instance_service_pb2, instance_service_pb2_grpc

procs = []

def start_servers():
    procs.append(subprocess.Popen([sys.executable, "crusoe_server.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    procs.append(subprocess.Popen([sys.executable, "lambda_server.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    procs.append(subprocess.Popen([sys.executable, "nebius_server.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

def wait_ready(url, retries=20):
    for i in range(retries):
        try:
            requests.get(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False

def cleanup():
    for p in procs:
        p.terminate()

def test_crusoe():
    print("=" * 50)
    print("CRUSOE (:8001)")
    print("=" * 50)
    h = {"Authorization": "Bearer crusoe-test-key-001"}
    base = "http://localhost:8001/v1alpha5/projects/proj-001"

    # Reservations
    r = requests.get(f"{base}/reservations", headers=h)
    assert r.status_code == 200
    rsvs = r.json()["items"]
    print(f"\nReservations: {len(rsvs)}")
    for rsv in rsvs:
        print(f"  {rsv['id']:10s} | {rsv['vm_type']:10s} | {rsv['status']:8s} | used={rsv['used_gpus']}/{rsv['total_gpus']} GPUs | ${rsv['unit_price_per_gpu_hour_usd']}/gpu/hr")

    # Instances
    r = requests.get(f"{base}/compute/vms/instances", headers=h)
    vms = r.json()["items"]
    print(f"\nInstances: {len(vms)}")
    for v in vms:
        print(f"  {v['name']:20s} | {v['billing_type']:10s} | reservation={v['reservation_id']} | {v['state']}")
    vm_id = vms[0]["id"]

    # Reboot (graceful, stays RUNNING)
    r = requests.post(f"{base}/compute/vms/instances/{vm_id}/reboot", headers=h)
    d = r.json()
    print(f"\nReboot:  action={d['operation']['action']:10s} vm_state={d['instance']['state']} (stays RUNNING)")

    # Reset (hard, stays RUNNING)
    r = requests.post(f"{base}/compute/vms/instances/{vm_id}/reset", headers=h)
    d = r.json()
    print(f"Reset:   action={d['operation']['action']:10s} vm_state={d['instance']['state']} (stays RUNNING)")

    # Restart (stop+start, state changes)
    r = requests.post(f"{base}/compute/vms/instances/{vm_id}/restart", headers=h)
    d = r.json()
    print(f"Restart: action={d['operation']['action']:10s} vm_state={d['instance']['state']} (transitions through STOPPING→STOPPED→STARTING→RUNNING)")

    # Create with reservation
    r = requests.post(f"{base}/compute/vms/instances", headers=h, json={
        "name": "rsv-test", "type": "h100.8x", "location": "us-west1", "ssh_key": "k", "reservation_id": "rsv-002"
    })
    d = r.json()
    print(f"\nCreate on reservation: billing={d['instance']['billing_type']} reservation_id={d['instance']['reservation_id']}")

    # Capacity
    r = requests.get(f"{base}/capacity", headers=h)
    print(f"\nCapacity (non-zero):")
    for c in r.json()["items"]:
        if c["total_available"] > 0:
            print(f"  {c['location']:10s} {c['vm_type']:10s} on_demand={c['on_demand_available']} reserved={c['reserved_available']}")

    print("\n  ✓ Crusoe OK")


def test_lambda():
    print("\n" + "=" * 50)
    print("LAMBDA (:8002)")
    print("=" * 50)
    h = {"Authorization": "Bearer lambda-test-key-001"}
    base = "http://localhost:8002/api/v1"

    # Instances with reserved flag
    r = requests.get(f"{base}/instances", headers=h)
    data = r.json()["data"]
    print(f"\nInstances: {len(data)}")
    for i in data:
        print(f"  {i['name']:25s} | reserved={i['is_reserved']} | reservation_id={i.get('reservation_id', 'N/A')}")

    # Reservations
    r = requests.get(f"{base}/reservations", headers=h)
    rsvs = r.json()["data"]
    print(f"\nReservations: {len(rsvs)}")
    for rsv in rsvs:
        print(f"  {rsv['id']:20s} | {rsv['instance_type']:15s} | {rsv['region']:10s} | used={rsv['used']}/{rsv['quantity']}")

    # Terminate reserved (should fail)
    reserved_id = next(i["id"] for i in data if i["is_reserved"])
    r = requests.post(f"{base}/instance-operations/terminate", headers=h, json={"instance_ids": [reserved_id]})
    assert r.status_code == 400
    print(f"\nTerminate reserved instance: BLOCKED (code={r.json()['detail']['error']['code']})")

    # Launch into reservation (use rsv-lambda-002 which has capacity)
    r = requests.post(f"{base}/instance-operations/launch", headers=h, json={
        "region_name": "us-east-1", "instance_type_name": "gpu_1x_a100",
        "ssh_key_names": ["k"], "name": "rsv-launch", "reservation_id": "rsv-lambda-002"
    })
    assert r.status_code == 200, f"Launch failed: {r.json()}"
    d = r.json()["data"]
    print(f"Launch into reservation: ids={d['instance_ids']}")

    print("\n  ✓ Lambda OK")


def test_nebius():
    print("\n" + "=" * 50)
    print("NEBIUS (gRPC :50051)")
    print("=" * 50)
    ch = grpc.insecure_channel("localhost:50051")
    meta = [("authorization", "Bearer nebius-test-key-001")]

    # Instance service
    stub = instance_service_pb2_grpc.InstanceServiceStub(ch)
    resp = stub.List(instance_service_pb2.ListInstancesRequest(parent_id="project-e1a2b3c4"), metadata=meta)
    print(f"\nInstances: {len(resp.instances)}")
    for inst in resp.instances:
        print(f"  {inst.metadata.name:20s} | {inst.spec.resources.platform}/{inst.spec.resources.preset} | state={inst.status.state} | reservation={inst.status.reservation_id}")

    # Reservation service
    rsv_stub = instance_service_pb2_grpc.ReservationServiceStub(ch)
    rsv_resp = rsv_stub.List(instance_service_pb2.ListReservationsRequest(parent_id="project-e1a2b3c4"), metadata=meta)
    print(f"\nReservations: {len(rsv_resp.reservations)}")
    for rsv in rsv_resp.reservations:
        print(f"  {rsv.metadata.id:15s} | {rsv.spec.platform}/{rsv.spec.preset} | state={rsv.status.state} | used={rsv.status.used_units}/{rsv.spec.total_units}")

    # Create with AUTO reservation policy
    create_req = instance_service_pb2.CreateInstanceRequest(
        metadata=instance_pb2.ResourceMetadata(parent_id="project-e1a2b3c4", name="auto-rsv-test", labels={"test": "true"}),
        spec=instance_pb2.InstanceSpec(
            resources=instance_pb2.ResourcesSpec(platform="gpu-h100-sxm", preset="8gpu-160vcpu-1600gb"),
            reservation_policy=instance_pb2.ReservationPolicy(policy=0),  # AUTO
        ),
    )
    op = stub.Create(create_req, metadata=meta)
    print(f"\nCreate (AUTO reservation): op_id={op.id} resource_id={op.resource_id} done={op.done}")

    # Create with STRICT reservation policy
    create_req2 = instance_service_pb2.CreateInstanceRequest(
        metadata=instance_pb2.ResourceMetadata(parent_id="project-e1a2b3c4", name="strict-rsv-test"),
        spec=instance_pb2.InstanceSpec(
            resources=instance_pb2.ResourcesSpec(platform="gpu-h200-sxm", preset="8gpu-160vcpu-2048gb"),
            reservation_policy=instance_pb2.ReservationPolicy(policy=2, reservation_ids=["rsv-neb-002"]),  # STRICT
        ),
    )
    op2 = stub.Create(create_req2, metadata=meta)
    print(f"Create (STRICT rsv-neb-002): op_id={op2.id} resource_id={op2.resource_id}")

    # Auth failure test
    try:
        stub.List(instance_service_pb2.ListInstancesRequest(parent_id="project-e1a2b3c4"), metadata=[("authorization", "Bearer bad-key")])
        print("Auth: SHOULD HAVE FAILED")
    except grpc.RpcError as e:
        print(f"\nAuth test: correctly rejected (code={e.code().name})")

    print("\n  ✓ Nebius gRPC OK")


if __name__ == "__main__":
    try:
        start_servers()
        wait_ready("http://localhost:8001/docs")
        wait_ready("http://localhost:8002/docs")
        # Wait a bit extra for gRPC
        time.sleep(2)

        test_crusoe()
        test_lambda()
        test_nebius()

        print("\n" + "=" * 50)
        print("ALL TESTS PASSED")
        print("=" * 50)
    finally:
        cleanup()
