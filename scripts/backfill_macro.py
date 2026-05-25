"""Backfill macro series. Vintage-tracked series ingest every ALFRED release;
others ingest the latest view.
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from substrate.config import load_macro_series
from substrate.ingest.macro import ingest_latest, ingest_macro_universe, ingest_vintages


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--series", nargs="*", help="Optional subset; defaults to the full macro_series.toml.")
    args = parser.parse_args()

    if not os.environ.get("FRED_API_KEY") or os.environ.get("FRED_API_KEY") == "your_fred_key_here":
        print("FRED_API_KEY not set in .env — get one (free) from https://fred.stlouisfed.org/docs/api/api_key.html")
        return 1

    all_series = load_macro_series()
    if args.series:
        chosen = [s for s in all_series if s.id in args.series]
        if not chosen:
            print(f"No series matched {args.series}")
            return 1
        total = 0
        for s in chosen:
            if s.vintage_tracked:
                total += ingest_vintages(s.id, years=args.years)
            else:
                total += ingest_latest(s.id, years=args.years)
        print(f"Done. {total} rows.")
        return 0

    print(f"Ingesting {len(all_series)} macro series for {args.years} years.")
    total = ingest_macro_universe(years=args.years)
    print(f"Done. {total} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
