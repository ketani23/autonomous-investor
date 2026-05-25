"""Backfill historical prices for the universe (or a subset)."""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from substrate.config import universe_tickers
from substrate.ingest.prices import ingest_universe


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument(
        "--symbols",
        nargs="*",
        help="Optional subset; defaults to the full universe.toml.",
    )
    args = parser.parse_args()

    symbols = args.symbols or universe_tickers()
    print(f"Ingesting {len(symbols)} symbols for {args.years} years: {', '.join(symbols)}")
    total = ingest_universe(symbols, years=args.years)
    print(f"Done. {total} total rows inserted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
