"""Presence records in Redis (docs/data-model.md §1). Uses fakeredis: presence only
needs plain hash commands, which fakeredis supports.
"""

from __future__ import annotations

import redis.asyncio as redis

from beam_server.store.presence_repo import PresenceRepository

# `fake_redis` comes from server/tests/conftest.py.


async def test_set_online_then_get_round_trips(fake_redis: redis.Redis) -> None:
    repo = PresenceRepository(fake_redis)
    await repo.set_online(room_id="r1", peer_id="p1", role="creator", instance_id="i1")

    record = await repo.get(room_id="r1", peer_id="p1")

    assert record is not None
    assert record.role == "creator"
    assert record.state == "online"
    assert record.instance_id == "i1"


async def test_get_of_unknown_peer_is_none(fake_redis: redis.Redis) -> None:
    repo = PresenceRepository(fake_redis)
    assert await repo.get(room_id="r1", peer_id="nobody") is None


async def test_set_reconnecting_preserves_the_other_fields(fake_redis: redis.Redis) -> None:
    repo = PresenceRepository(fake_redis)
    await repo.set_online(room_id="r1", peer_id="p1", role="joiner", instance_id="i1")

    await repo.set_reconnecting(room_id="r1", peer_id="p1")

    record = await repo.get(room_id="r1", peer_id="p1")
    assert record is not None
    assert record.state == "reconnecting"
    assert record.role == "joiner"
    assert record.instance_id == "i1"


async def test_refresh_extends_ttl_without_changing_state(fake_redis: redis.Redis) -> None:
    repo = PresenceRepository(fake_redis)
    await repo.set_online(room_id="r1", peer_id="p1", role="creator", instance_id="i1")

    await repo.refresh(room_id="r1", peer_id="p1")

    record = await repo.get(room_id="r1", peer_id="p1")
    assert record is not None
    assert record.state == "online"
    assert record.role == "creator"


async def test_delete_removes_the_record(fake_redis: redis.Redis) -> None:
    repo = PresenceRepository(fake_redis)
    await repo.set_online(room_id="r1", peer_id="p1", role="creator", instance_id="i1")

    await repo.delete(room_id="r1", peer_id="p1")

    assert await repo.get(room_id="r1", peer_id="p1") is None


async def test_get_of_a_partial_record_is_none_not_a_crash(fake_redis: redis.Redis) -> None:
    """A real bug, found via CI (not locally -- see ws/endpoint.py's presence-heartbeat
    comment): set_reconnecting() re-creates the presence key with only `state` if its
    TTL had already lapsed (nothing was refreshing it), and PresenceRepository.get()
    used to raise a bare KeyError on `role` reading that back. A partial record is
    exactly as meaningless as no record -- get() must return None for both."""
    repo = PresenceRepository(fake_redis)
    await repo.set_reconnecting(room_id="r1", peer_id="ghost")  # key never existed

    assert await repo.get(room_id="r1", peer_id="ghost") is None
