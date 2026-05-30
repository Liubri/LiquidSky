"""Command-line entry point.

    python -m liquidsky.cli once    --env paper
    python -m liquidsky.cli run     --env paper
    python -m liquidsky.cli status  --env paper
    python -m liquidsky.cli report  --env paper
    python -m liquidsky.cli serve   --env paper   # web dashboard

`--env` overrides KALSHI_ENV; it defaults to paper (no orders sent).
"""
from __future__ import annotations

import argparse
import logging
import sys

from .bot import Bot
from .config import VALID_ENVS, load_config


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="liquidsky", description="Kalshi weather trading bot")
    p.add_argument("command", choices=["once", "run", "status", "report", "serve"])
    p.add_argument("--env", choices=VALID_ENVS, default=None,
                   help="trading environment (default: KALSHI_ENV or paper)")
    p.add_argument("--host", default="127.0.0.1", help="serve: bind host")
    p.add_argument("--port", type=int, default=8787, help="serve: bind port")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)

    cfg = load_config(env=args.env)
    bot = Bot(cfg)

    if args.command == "status":
        print(bot.status())
    elif args.command == "report":
        print(bot.report())
    elif args.command == "once":
        bot.run_once()
        print(bot.status())
    elif args.command == "run":
        bot.run_forever()
    elif args.command == "serve":
        from .web import serve
        url = f"http://{args.host}:{args.port}"
        print(f"LiquidSky dashboard [env={cfg.env}] -> {url}")
        serve(cfg, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
