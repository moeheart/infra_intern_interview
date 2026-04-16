# VM CLI Skeleton

This is a Python CLI skeleton for Layer 1 with three providers:

- `crusoe`
- `lambda`
- `nebius`

Crusoe and Lambda use the Python standard library.
Nebius requires `grpcio` and `protobuf`.

## Run

From the repository root:

```bash
python candidate/vm.py list
python candidate/vm.py list --provider lambda
python candidate/vm.py create --provider crusoe --gpu h100.1x --count 1 --region us-west
python candidate/vm.py create --provider nebius --gpu h100.1x --count 1 --name nb-test
python candidate/vm.py get <instance_id> --provider lambda
python candidate/vm.py destroy <instance_id> --provider lambda
```

If you want Nebius support in your local Python environment:

```bash
python -m pip install -r candidate/requirements.txt
```

## Defaults

The CLI is preconfigured for the mock servers:

- Crusoe: `http://localhost:8001`, project `proj-001`
- Lambda: `http://localhost:8002`
- Nebius: `grpc://localhost:50051`, parent `project-e1a2b3c4`

Override them with environment variables if needed:

```bash
VM_CLI_CRUSOE_BASE_URL
VM_CLI_CRUSOE_API_KEY
VM_CLI_CRUSOE_PROJECT_ID
VM_CLI_LAMBDA_BASE_URL
VM_CLI_LAMBDA_API_KEY
VM_CLI_NEBIUS_ENDPOINT
VM_CLI_NEBIUS_API_KEY
VM_CLI_NEBIUS_PARENT_ID
VM_CLI_DEFAULT_SSH_KEY
```

## Unified CLI Choices

- Canonical GPU names: `a100.1x`, `a100.8x`, `h100.1x`, `h100.8x`, `h200.1x`, `h200.8x`
- Canonical regions: `us-west`, `us-east`, `eu-west`
- `lambda` does not support `start` or `stop`, so those commands return a clear unsupported error.
- Nebius is parent-scoped, so the normalized region is reported as `global`.

## Suggested Next Steps

1. Add Nebius as the third provider.
2. Add `vm fleet ...` state management.
3. Persist fleet membership in SQLite or a local JSON file.
4. Add retry and concurrent create behavior for fleet operations.
