"""Keystone HTTP API.

For now this only wires the graph database up and reports health. The full Graphiti client
(which needs an LLM API key) is created in the graph engine (KAN-23).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from graphiti_core.driver.neo4j_driver import Neo4jDriver

from keystone import __version__
from keystone.config import get_settings

logger = logging.getLogger("keystone.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    driver = Neo4jDriver(
        uri=settings.neo4j_uri,
        user=settings.neo4j_user,
        password=settings.neo4j_password.get_secret_value(),
        database=settings.neo4j_database,
    )
    app.state.driver = driver
    try:
        # Graphiti's indexes and constraints; safe to run on every start.
        await driver.build_indices_and_constraints()
        logger.info("Graphiti indices and constraints ready")
    except Exception:
        # Keep the API up so /health can report the problem instead of crash-looping.
        logger.exception("Could not prepare graph database")
    yield
    await driver.close()


app = FastAPI(title="Keystone API", version=__version__, lifespan=lifespan)


@app.get("/health")
async def health(request: Request) -> JSONResponse:
    driver: Neo4jDriver = request.app.state.driver
    body: dict[str, Any] = {"status": "ok", "version": __version__, "neo4j": "ok"}
    try:
        await driver.health_check()
    except Exception as exc:
        body.update(status="degraded", neo4j=f"error: {type(exc).__name__}")
        return JSONResponse(body, status_code=503)
    return JSONResponse(body)
