from __future__ import annotations

import os
from dataclasses import dataclass


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
class AppConfig:
    crusoe: CrusoeConfig
    lambda_cloud: LambdaConfig
    default_ssh_key: str


def _env(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    return value or default


def load_config() -> AppConfig:
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
        default_ssh_key=_env("VM_CLI_DEFAULT_SSH_KEY", "default-key"),
    )
