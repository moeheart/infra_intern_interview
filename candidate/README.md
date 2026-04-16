# VM CLI Skeleton

This is a Python CLI skeleton for Layer 1 with the first two providers:

- `crusoe`
- `lambda`

It uses only the Python standard library, so there are no extra dependencies to install.

## Run

From the repository root:

```bash
python candidate/vm.py list
python candidate/vm.py list --provider lambda
python candidate/vm.py create --provider crusoe --gpu h100.1x --count 1 --region us-west
python candidate/vm.py get <instance_id> --provider lambda
python candidate/vm.py destroy <instance_id> --provider lambda
```

## Defaults

The CLI is preconfigured for the mock servers:

- Crusoe: `http://localhost:8001`, project `proj-001`
- Lambda: `http://localhost:8002`

Override them with environment variables if needed:

```bash
VM_CLI_CRUSOE_BASE_URL
VM_CLI_CRUSOE_API_KEY
VM_CLI_CRUSOE_PROJECT_ID
VM_CLI_LAMBDA_BASE_URL
VM_CLI_LAMBDA_API_KEY
VM_CLI_DEFAULT_SSH_KEY
```

## Unified CLI Choices

- Canonical GPU names: `a100.1x`, `a100.8x`, `h100.1x`, `h100.8x`
- Canonical regions: `us-west`, `us-east`, `eu-west`
- `lambda` does not support `start` or `stop`, so those commands return a clear unsupported error.

## Suggested Next Steps

1. Add Nebius as the third provider.
2. Add `vm fleet ...` state management.
3. Persist fleet membership in SQLite or a local JSON file.
4. Add retry and concurrent create behavior for fleet operations.
