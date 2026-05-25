"""Top-level pytest conftest. Loads .env so tests that touch the DB or
external APIs find their credentials.
"""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
