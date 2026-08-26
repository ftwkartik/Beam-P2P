"""Prometheus scrape endpoint (docs/protocol.md §2: "internal network only").

Not under `/api/v1` -- Prometheus scrapers expect `/metrics` at the root by
convention, and this isn't part of the versioned client-facing API surface. It is not
access-controlled here; docs/security.md's threat model places that responsibility on
network placement (a scraper on an internal network, not exposed publicly), the same
way most Prometheus exporters work.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from beam_server.observability.metrics import registry

router = APIRouter(tags=["ops"])


@router.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
