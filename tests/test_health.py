import httpx

from keystone.api.main import app


class FakeDriver:
    def __init__(self, healthy: bool) -> None:
        self.healthy = healthy

    async def health_check(self) -> None:
        if not self.healthy:
            raise ConnectionError("neo4j down")


async def get_health(healthy: bool) -> httpx.Response:
    app.state.driver = FakeDriver(healthy)
    transport = httpx.ASGITransport(app=app)  # does not run lifespan: no real Neo4j needed
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/health")


async def test_health_ok() -> None:
    response = await get_health(healthy=True)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_health_reports_neo4j_failure() -> None:
    response = await get_health(healthy=False)
    assert response.status_code == 503
    assert response.json()["neo4j"].startswith("error")
