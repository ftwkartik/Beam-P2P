"""The uniform error envelope for 404s, validation errors and unhandled exceptions."""

from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from beam_server.errors import NotFoundError, register_exception_handlers
from beam_server.observability.middleware import RequestContextMiddleware


def _envelope_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    @app.get("/missing-thing/{thing_id}")
    async def missing_thing(thing_id: str) -> None:
        raise NotFoundError("Thing not found.", details={"thing_id": thing_id})

    @app.get("/needs-int")
    async def needs_int(n: int) -> dict[str, int]:
        return {"n": n}

    return app


async def test_unknown_route_returns_envelope() -> None:
    app = _envelope_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert "request_id" in body["error"]


async def test_domain_error_maps_to_status_and_code() -> None:
    app = _envelope_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/missing-thing/abc123")

    assert response.status_code == 404
    body = response.json()
    assert body["error"] == {
        "code": "NOT_FOUND",
        "message": "Thing not found.",
        "details": {"thing_id": "abc123"},
        "request_id": body["error"]["request_id"],
    }
    assert body["error"]["request_id"]


async def test_validation_error_returns_422_with_field_errors() -> None:
    app = _envelope_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/needs-int", params={"n": "not-a-number"})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"]["errors"]


async def test_unhandled_exception_returns_500_without_leaking_details() -> None:
    app = _envelope_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "kaboom" not in response.text
    assert "Traceback" not in response.text
