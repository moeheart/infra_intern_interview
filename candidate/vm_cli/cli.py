from __future__ import annotations

import argparse
import sys

from vm_cli.config import load_config
from vm_cli.errors import CliError, ProviderError, UnsupportedOperationError
from vm_cli.models import CreateRequest
from vm_cli.output import emit
from vm_cli.providers import CrusoeProvider, LambdaProvider

PROVIDER_CHOICES = ("crusoe", "lambda")
GPU_CHOICES = ("a100.1x", "a100.8x", "h100.1x", "h100.8x")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return 1

    providers = build_providers()

    try:
        if args.command == "list":
            provider_names = [args.provider] if args.provider else list(PROVIDER_CHOICES)
            records = []
            for provider_name in provider_names:
                records.extend(providers[provider_name].list_instances())
            emit(records, args.json)
            return 0

        provider = providers[args.provider]

        if args.command == "create":
            if args.count < 1:
                raise CliError("--count must be at least 1")
            req = CreateRequest(
                provider=args.provider,
                gpu=args.gpu,
                count=args.count,
                name=args.name,
                region=args.region,
                ssh_key=args.ssh_key,
                reservation_id=args.reservation_id,
            )
            emit(provider.create_instances(req), args.json)
            return 0

        if args.command == "get":
            emit(provider.get_instance(args.instance_id), args.json)
            return 0

        if args.command == "stop":
            emit(provider.stop_instance(args.instance_id), args.json)
            return 0

        if args.command == "start":
            emit(provider.start_instance(args.instance_id), args.json)
            return 0

        if args.command == "destroy":
            emit(provider.destroy_instance(args.instance_id), args.json)
            return 0

        parser.error(f"Unknown command: {args.command}")
        return 1
    except UnsupportedOperationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (CliError, ProviderError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vm", description="Unified VM CLI skeleton")
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="List instances")
    list_parser.add_argument("--provider", choices=PROVIDER_CHOICES)
    _add_json_flag(list_parser)

    create_parser = subparsers.add_parser("create", help="Create instances")
    create_parser.add_argument("--provider", choices=PROVIDER_CHOICES, required=True)
    create_parser.add_argument("--gpu", choices=GPU_CHOICES, required=True)
    create_parser.add_argument("--count", type=int, required=True)
    create_parser.add_argument("--name")
    create_parser.add_argument("--region", default="us-west")
    create_parser.add_argument("--ssh-key", default=load_config().default_ssh_key)
    create_parser.add_argument("--reservation-id")
    _add_json_flag(create_parser)

    get_parser = subparsers.add_parser("get", help="Get instance details")
    get_parser.add_argument("instance_id")
    get_parser.add_argument("--provider", choices=PROVIDER_CHOICES, required=True)
    _add_json_flag(get_parser)

    stop_parser = subparsers.add_parser("stop", help="Stop an instance")
    stop_parser.add_argument("instance_id")
    stop_parser.add_argument("--provider", choices=PROVIDER_CHOICES, required=True)
    _add_json_flag(stop_parser)

    start_parser = subparsers.add_parser("start", help="Start an instance")
    start_parser.add_argument("instance_id")
    start_parser.add_argument("--provider", choices=PROVIDER_CHOICES, required=True)
    _add_json_flag(start_parser)

    destroy_parser = subparsers.add_parser("destroy", help="Destroy an instance")
    destroy_parser.add_argument("instance_id")
    destroy_parser.add_argument("--provider", choices=PROVIDER_CHOICES, required=True)
    _add_json_flag(destroy_parser)

    return parser


def build_providers() -> dict[str, object]:
    config = load_config()
    return {
        "crusoe": CrusoeProvider(config.crusoe),
        "lambda": LambdaProvider(config.lambda_cloud),
    }


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print normalized JSON")
