"""pygola command-line interface.

Usage:
    pygola serve --config policy.yaml
    pygola serve --host 0.0.0.0 --port 8000
    pygola serve --config policy.yaml --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import argparse
import sys


def _cmd_serve(args: argparse.Namespace) -> None:
    try:
        import uvicorn
        from pygola.server.app import create_app, ServerConfig
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(
            "Install server extras with:  pip install 'pygola[server]'",
            file=sys.stderr,
        )
        sys.exit(1)

    from pygola import GovernanceLayer

    layer = GovernanceLayer.from_config(args.config)
    config = ServerConfig(host=args.host, port=args.port)
    application = create_app(layer, config)
    uvicorn.run(application, host=args.host, port=args.port)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pygola",
        description="pygola — configurable AI governance layer",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    serve = subparsers.add_parser("serve", help="Start the governance layer HTTP server")
    serve.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="Path to a policy YAML/JSON config file (uses defaults if omitted)",
    )
    serve.add_argument(
        "--host",
        default="0.0.0.0",
        metavar="HOST",
        help="Interface to bind on (default: 0.0.0.0)",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=8000,
        metavar="PORT",
        help="Port to listen on (default: 8000)",
    )

    args = parser.parse_args()

    if args.command == "serve":
        _cmd_serve(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
