from __future__ import annotations

import importlib.util
import sys
import unittest
import uuid
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vm_cli.config import load_config
from vm_cli.errors import ProviderError
from vm_cli.models import CreateRequest
from vm_cli.providers.nebius import NebiusProvider


@unittest.skipUnless(importlib.util.find_spec("grpc"), "grpcio is not installed")
class NebiusProviderIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.provider = NebiusProvider(load_config().nebius)
        try:
            cls.seed_instances = cls.provider.list_instances()
        except ProviderError as exc:  # pragma: no cover - depends on local server
            raise unittest.SkipTest(f"Nebius mock server is not reachable: {exc}") from exc

    def test_list_instances(self) -> None:
        self.assertGreaterEqual(len(self.seed_instances), 1)
        self.assertTrue(any(item.provider == "nebius" for item in self.seed_instances))

    def test_create_stop_start_destroy_cycle(self) -> None:
        instance_id = None
        try:
            created = self.provider.create_instances(
                CreateRequest(
                    provider="nebius",
                    gpu="h100.1x",
                    count=1,
                    name=f"nb-test-{uuid.uuid4().hex[:8]}",
                    region=None,
                    ssh_key="unused",
                )
            )[0]
            instance_id = created.id
            self.assertEqual(created.state, "running")
            self.assertEqual(created.gpu, "h100.1x")

            fetched = self.provider.get_instance(instance_id)
            self.assertEqual(fetched.id, instance_id)

            stopped = self.provider.stop_instance(instance_id)
            self.assertEqual(stopped.state, "stopped")

            started = self.provider.start_instance(instance_id)
            self.assertEqual(started.state, "running")

            deleted = self.provider.destroy_instance(instance_id)
            self.assertEqual(deleted.state, "deleted")

            with self.assertRaises(ProviderError):
                self.provider.get_instance(instance_id)
        finally:
            if instance_id:
                try:
                    self.provider.destroy_instance(instance_id)
                except ProviderError:
                    pass

    def test_create_with_strict_reservation(self) -> None:
        instance_id = None
        try:
            created = self.provider.create_instances(
                CreateRequest(
                    provider="nebius",
                    gpu="h200.8x",
                    count=1,
                    name=f"nb-rsv-{uuid.uuid4().hex[:8]}",
                    region=None,
                    ssh_key="unused",
                    reservation_id="rsv-neb-002",
                )
            )[0]
            instance_id = created.id
            self.assertEqual(created.billing_type, "reserved")
            self.assertEqual(created.reservation_id, "rsv-neb-002")
        finally:
            if instance_id:
                try:
                    self.provider.destroy_instance(instance_id)
                except ProviderError:
                    pass

    def test_destroy_missing_instance_returns_provider_error(self) -> None:
        with self.assertRaises(ProviderError) as ctx:
            self.provider.destroy_instance(str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "NOT_FOUND")
