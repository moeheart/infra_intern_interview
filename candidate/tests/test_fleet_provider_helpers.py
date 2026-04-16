from __future__ import annotations

import unittest
from unittest.mock import patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vm_cli.config import CrusoeConfig, LambdaConfig, NebiusConfig
from vm_cli.errors import ProviderError
from vm_cli.models import CreateRequest, InstanceRecord
from vm_cli.providers.crusoe import CrusoeProvider
from vm_cli.providers.lambda_cloud import LambdaProvider
from vm_cli.providers.nebius import NebiusProvider


def _instance(provider: str, name: str, region: str = "us-west") -> InstanceRecord:
    return InstanceRecord(
        provider=provider,
        id=f"{provider}-{name}",
        name=name,
        gpu="h100.8x",
        provider_gpu="h100.8x",
        region=region,
        provider_region=region,
        state="running",
        public_ip=None,
        private_ip=None,
        reservation_id=None,
        billing_type="on_demand",
    )


class FleetProviderHelpersTest(unittest.TestCase):
    def test_crusoe_capacity_mapping_uses_total_available(self) -> None:
        provider = CrusoeProvider(
            CrusoeConfig(base_url="http://example", api_key="k", project_id="p")
        )
        payload = {
            "items": [
                {"location": "us-west1", "vm_type": "h100.8x", "total_available": 3},
                {"location": "us-east1", "vm_type": "a100.8x", "total_available": 5},
            ]
        }

        with patch.object(provider, "_request", return_value=payload):
            records = provider.list_capacity("h100.8x")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].region, "us-west")
        self.assertEqual(records[0].available, 3)
        self.assertEqual(records[0].certainty, "exact")

    def test_lambda_best_effort_retries_with_reported_available_capacity(self) -> None:
        provider = LambdaProvider(LambdaConfig(base_url="http://example", api_key="k"))
        request = CreateRequest(
            provider="lambda",
            gpu="h100.8x",
            count=4,
            name="fleet-lambda",
            region="us-west",
            ssh_key="default-key",
        )
        retried_instances = [_instance("lambda", "fleet-lambda-1"), _instance("lambda", "fleet-lambda-2")]

        with patch.object(
            provider,
            "create_instances",
            side_effect=[
                ProviderError(
                    "lambda",
                    "Insufficient capacity. Requested 4, available 2.",
                    code="capacity",
                ),
                retried_instances,
            ],
        ) as create_mock:
            created = provider.create_instances_best_effort(request)

        self.assertEqual(created, retried_instances)
        self.assertEqual(create_mock.call_args_list[1].args[0].count, 2)

    def test_lambda_capacity_records_are_unknown_on_demand_candidates(self) -> None:
        provider = LambdaProvider(LambdaConfig(base_url="http://example", api_key="k"))
        payload = {
            "data": {
                "gpu_8x_h100": {
                    "regions_with_capacity_available": [
                        {"name": "us-west-1", "description": "California"},
                        {"name": "eu-west-1", "description": "London"},
                    ]
                }
            }
        }

        with patch.object(provider, "_request", return_value=payload):
            records = provider.list_capacity("h100.8x")

        self.assertEqual([(item.region, item.available, item.certainty) for item in records], [
            ("us-west", None, "unknown"),
            ("eu-west", None, "unknown"),
        ])

    def test_nebius_best_effort_stops_after_capacity_error(self) -> None:
        provider = NebiusProvider(
            NebiusConfig(endpoint="grpc://localhost:50051", api_key="k", parent_id="p")
        )
        request = CreateRequest(
            provider="nebius",
            gpu="h100.8x",
            count=3,
            name="fleet-nebius",
            region="global",
            ssh_key="default-key",
        )

        with patch.object(
            provider,
            "create_instances",
            side_effect=[
                [_instance("nebius", "fleet-nebius-1", region="global")],
                ProviderError("nebius", "No capacity left", code="capacity"),
            ],
        ) as create_mock:
            created = provider.create_instances_best_effort(request)

        self.assertEqual(len(created), 1)
        self.assertEqual(create_mock.call_count, 2)
