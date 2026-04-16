from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vm_cli.errors import ProviderError, UnsupportedOperationError


class ErrorFormattingTest(unittest.TestCase):
    def test_crusoe_auth_error_uses_unified_code(self) -> None:
        err = ProviderError(
            "crusoe",
            "Invalid API key",
            code="UNAUTHENTICATED",
            status=401,
        )
        self.assertEqual(err.code, "authentication")
        self.assertEqual(
            str(err),
            "Error [authentication] (crusoe): Invalid API key",
        )

    def test_lambda_auth_error_uses_unified_code(self) -> None:
        err = ProviderError(
            "lambda",
            "API key was invalid, expired, or deleted.",
            code="global/invalid-api-key",
            status=401,
            suggestion="Check your API key or create a new one, then try again.",
        )
        self.assertEqual(err.code, "authentication")
        self.assertEqual(
            str(err),
            "Error [authentication] (lambda): API key was invalid, expired, or deleted.\n"
            "Hint: Check your API key or create a new one, then try again.",
        )

    def test_nebius_auth_error_uses_unified_code(self) -> None:
        err = ProviderError(
            "nebius",
            "Invalid or missing API key",
            code="UNAUTHENTICATED",
        )
        self.assertEqual(err.code, "authentication")
        self.assertEqual(
            str(err),
            "Error [authentication] (nebius): Invalid or missing API key",
        )

    def test_lambda_error_uses_unified_code(self) -> None:
        err = ProviderError(
            "lambda",
            "Instance abc123 does not exist.",
            code="global/object-does-not-exist",
            status=404,
        )
        self.assertEqual(err.code, "not_found")
        self.assertEqual(
            str(err),
            "Error [not_found] (lambda): Instance abc123 does not exist.",
        )

    def test_crusoe_not_found_uses_unified_code(self) -> None:
        err = ProviderError("crusoe", "Instance vm-123 not found", code="NOT_FOUND", status=404)
        self.assertEqual(err.code, "not_found")
        self.assertEqual(
            str(err),
            "Error [not_found] (crusoe): Instance vm-123 not found",
        )

    def test_nebius_not_found_uses_unified_code(self) -> None:
        err = ProviderError("nebius", "Instance vm-123 not found", code="NOT_FOUND")
        self.assertEqual(err.code, "not_found")
        self.assertEqual(
            str(err),
            "Error [not_found] (nebius): Instance vm-123 not found",
        )

    def test_grpc_capacity_error_uses_unified_code(self) -> None:
        err = ProviderError("nebius", "No capacity available.", code="RESOURCE_EXHAUSTED")
        self.assertEqual(err.code, "capacity")
        self.assertEqual(str(err), "Error [capacity] (nebius): No capacity available.")

    def test_crusoe_capacity_error_uses_unified_code(self) -> None:
        err = ProviderError(
            "crusoe",
            "No capacity for h100.8x in us-west1.",
            code="RESOURCE_EXHAUSTED",
            status=400,
        )
        self.assertEqual(err.code, "capacity")
        self.assertEqual(
            str(err),
            "Error [capacity] (crusoe): No capacity for h100.8x in us-west1.",
        )

    def test_suggestion_uses_shared_hint_line(self) -> None:
        err = ProviderError(
            "lambda",
            "Insufficient capacity.",
            code="instance-operations/launch/insufficient-capacity",
            suggestion="Try a different region or instance type.",
        )
        self.assertEqual(
            str(err),
            "Error [capacity] (lambda): Insufficient capacity.\nHint: Try a different region or instance type.",
        )

    def test_unsupported_operation_uses_same_style(self) -> None:
        err = UnsupportedOperationError("lambda", "stop")
        self.assertEqual(
            str(err),
            "Error [unsupported] (lambda): Operation 'stop' is not supported by this provider.",
        )
