"""
CLI entrypoint for local / VPS runs.

Usage:
    python -m booker.cli book   # run the booking flow
    python -m booker.cli seed   # log in once and persist the session

Configuration comes from environment variables (see booker/config.py) or a local
.env file. This module is intentionally thin: all logic lives in the package.
"""

import sys
import logging

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from . import (
    Orchestrator,
    get_provider,
    build_session_store_from_env,
)
from .config import (
    credentials_from_env,
    request_from_env,
    provider_name_from_env,
    orchestrator_config_from_env,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("booker.cli")


def _build() -> tuple[Orchestrator, "BookingRequest"]:
    provider = get_provider(provider_name_from_env())
    credentials = credentials_from_env()
    config = orchestrator_config_from_env()
    store = build_session_store_from_env(config.session_path)
    orch = Orchestrator(provider, credentials, store, config)
    return orch, request_from_env()


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "book"
    orch, request = _build()

    if command == "seed":
        result = orch.seed_session(request)
        log.info(f"Seed result: {result.to_dict()}")
        return 0 if result.ok else 1

    if command == "book":
        result = orch.book(request)
        log.info(f"Result: {result.to_dict()}")
        if result.ok:
            log.info(f"✅ SUCCESS — {request.activity} on "
                     f"{result.booked_date} at {result.booked_time}")
            return 0
        log.error(f"❌ FAILED — {result.outcome.value}: {result.detail}")
        return 1

    log.error(f"Unknown command: {command!r} (use 'book' or 'seed')")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
