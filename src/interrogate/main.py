"""InterroGate entry point."""

import logging

import uvicorn

from .config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


def main():
    """Run the InterroGate server."""
    settings = get_settings()

    if settings.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    uvicorn.run(
        "interrogate.mcp:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
