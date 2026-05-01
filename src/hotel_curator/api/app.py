"""FastAPI application factory."""

import logging

from fastapi import FastAPI

from hotel_curator.api.routes import health, hotels, reviews

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(
        title="Hotel Curator API",
        version="0.1.0",
        description=(
            "Multi-attribute hotel quality classifier and "
            "tier ranking system."
        ),
    )
    app.include_router(health.router)
    app.include_router(reviews.router)
    app.include_router(hotels.router)
    return app


app = create_app()
