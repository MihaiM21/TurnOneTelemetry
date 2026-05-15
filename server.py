"""Entry point. Run with `python server.py` or `make run`."""
import uvicorn

from src.api.app import create_app
from src.core.config import settings
from src.core.logging import get_logger

app = create_app()
logger = get_logger(__name__)

if __name__ == "__main__":
    logger.info(f"Starting server on {settings.host}:{settings.docker_exposed_port}")
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.docker_exposed_port,
        log_level=settings.log_level.lower(),
    )
