from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CrusoeConfig:
    base_url: str
    api_key: str
    project_id: str
    poll_interval_seconds: float = 1.0
    poll_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class LambdaConfig:
    base_url: str
    api_key: str


@dataclass(frozen=True)
class NebiusConfig:
    endpoint: str
    api_key: str
    parent_id: str
    poll_interval_seconds: float = 1.0
    poll_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class AppConfig:
    crusoe: CrusoeConfig
    lambda_cloud: LambdaConfig
    nebius: NebiusConfig
    default_ssh_key: str
    fleet_state_path: Path


def _env(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    return value or default


def load_config() -> AppConfig:
    candidate_dir = Path(__file__).resolve().parents[1]
    return AppConfig(
        crusoe=CrusoeConfig(
            base_url=_env("VM_CLI_CRUSOE_BASE_URL", "http://localhost:8001"),
            api_key=_env("VM_CLI_CRUSOE_API_KEY", "crusoe-test-key-001"),
            project_id=_env("VM_CLI_CRUSOE_PROJECT_ID", "proj-001"),
        ),
        lambda_cloud=LambdaConfig(
            base_url=_env("VM_CLI_LAMBDA_BASE_URL", "http://localhost:8002"),
            api_key=_env("VM_CLI_LAMBDA_API_KEY", "lambda-test-key-001"),
        ),
        nebius=NebiusConfig(
            endpoint=_env("VM_CLI_NEBIUS_ENDPOINT", "grpc://localhost:50051"),
            api_key=_env("VM_CLI_NEBIUS_API_KEY", "nebius-test-key-001"),
            parent_id=_env("VM_CLI_NEBIUS_PARENT_ID", "project-e1a2b3c4"),
        ),
        default_ssh_key=_env("VM_CLI_DEFAULT_SSH_KEY", "default-key"),
        fleet_state_path=Path(_env("VM_CLI_FLEET_STATE_PATH", str(candidate_dir / ".vm_fleets.json"))),
    )
