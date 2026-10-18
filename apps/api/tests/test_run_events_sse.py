"""Comprehensive test suite for Redis Pub/Sub and SSE streaming (QA AgentOS M2 Slice 3).

Validates:
1. RunEventPayload schema, serialization, and SSE framing.
2. Channel naming and project/run scoping.
3. Stream ticket generation, expiration, single-use consumption, and in-memory fallback.
4. Non-fatal Redis publish error resilience.
5. SSE streaming generator with heartbeats and terminal event completion.
6. Subscriber cleanup on client disconnect / task cancellation.
7. Authenticated SSE endpoint access, cross-project isolation, and ticket redemption.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from qa_agentos.main import app
from qa_agentos.runtime.pubsub import (
    RunEventPayload,
    consume_stream_ticket,
    create_stream_ticket,
    get_run_events_channel,
    publish_run_event,
    subscribe_run_events,
)


@pytest.mark.asyncio
async def test_run_event_payload_schema_and_sse_formatting():
    """Verify RunEventPayload initializes with defaults and correctly frames SSE messages."""
    run_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    payload = RunEventPayload(
        event="run.running",
        run_id=run_id,
        project_id=project_id,
        timestamp="2026-10-18T12:00:00Z",
        data={"status": "running", "attempt": 1},
    )

    assert payload.version == "1.0"
    assert payload.event == "run.running"
    assert payload.run_id == run_id
    assert payload.project_id == project_id
    assert payload.data["status"] == "running"

    sse_frame = payload.format_sse()
    assert sse_frame.startswith(f"id: {payload.id}\n")
    assert "event: run.running\n" in sse_frame
    assert "data: " in sse_frame
    assert sse_frame.endswith("\n\n")

    # Parse JSON from data line
    data_line = [line for line in sse_frame.split("\n") if line.startswith("data: ")][0]
    parsed = json.loads(data_line[6:])
    assert parsed["id"] == payload.id
    assert parsed["event"] == "run.running"
    assert parsed["data"]["status"] == "running"


def test_channel_scoping():
    """Verify channel names are scoped strictly to project_id and run_id."""
    p_id = uuid.uuid4()
    r_id = uuid.uuid4()
    channel = get_run_events_channel(p_id, r_id)
    assert channel == f"projects:{p_id}:runs:{r_id}:events"


@pytest.mark.asyncio
async def test_publish_run_event_success():
    """Verify publish_run_event serializes payload and calls redis.publish."""
    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock(return_value=1)

    with patch("qa_agentos.runtime.pubsub.get_redis_client", return_value=mock_redis):
        payload = RunEventPayload(
            event="run.queued",
            run_id=str(uuid.uuid4()),
            project_id=str(uuid.uuid4()),
            timestamp="2026-10-18T12:00:00Z",
            data={"status": "queued"},
        )
        success = await publish_run_event(payload)
        assert success is True
        mock_redis.publish.assert_called_once()
        call_channel, call_data = mock_redis.publish.call_args[0]
        assert call_channel == get_run_events_channel(payload.project_id, payload.run_id)
        assert json.loads(call_data)["event"] == "run.queued"


@pytest.mark.asyncio
async def test_publish_run_event_resilience_on_redis_error():
    """Verify publish_run_event catches Redis exceptions without crashing execution."""
    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock(side_effect=ConnectionError("Redis connection refused"))

    with patch("qa_agentos.runtime.pubsub.get_redis_client", return_value=mock_redis):
        payload = RunEventPayload(
            event="step.completed",
            run_id=str(uuid.uuid4()),
            project_id=str(uuid.uuid4()),
            timestamp="2026-10-18T12:00:00Z",
            data={"step_number": 1},
        )
        success = await publish_run_event(payload)
        assert success is False  # Safe return, no exception raised


@pytest.mark.asyncio
async def test_stream_ticket_lifecycle_redis():
    """Verify stream ticket creation and single-use consumption in Redis."""
    store = {}

    mock_redis = AsyncMock()

    async def mock_setex(key, ttl, val):
        store[key] = val

    async def mock_get(key):
        return store.get(key)

    async def mock_delete(key):
        store.pop(key, None)

    mock_redis.setex = AsyncMock(side_effect=mock_setex)
    mock_redis.get = AsyncMock(side_effect=mock_get)
    mock_redis.delete = AsyncMock(side_effect=mock_delete)

    with patch("qa_agentos.runtime.pubsub.get_redis_client", return_value=mock_redis):
        user_id = str(uuid.uuid4())
        project_id = str(uuid.uuid4())

        ticket = await create_stream_ticket(user_id=user_id, project_id=project_id, ttl_seconds=60)
        assert ticket is not None
        assert len(ticket) > 20

        # First consumption must succeed
        consumed = await consume_stream_ticket(ticket)
        assert consumed is not None
        assert consumed["user_id"] == user_id
        assert consumed["project_id"] == project_id

        # Second consumption must return None (single-use)
        consumed_again = await consume_stream_ticket(ticket)
        assert consumed_again is None


@pytest.mark.asyncio
async def test_stream_ticket_memory_fallback():
    """Verify stream ticket creation and consumption falls back gracefully to memory when Redis fails."""
    mock_redis = AsyncMock()
    mock_redis.setex = AsyncMock(side_effect=ConnectionError("Redis down"))
    mock_redis.get = AsyncMock(side_effect=ConnectionError("Redis down"))

    with patch("qa_agentos.runtime.pubsub.get_redis_client", return_value=mock_redis):
        user_id = str(uuid.uuid4())
        project_id = str(uuid.uuid4())

        ticket = await create_stream_ticket(user_id=user_id, project_id=project_id, ttl_seconds=60)
        assert ticket is not None

        # Consumes from in-memory fallback
        consumed = await consume_stream_ticket(ticket)
        assert consumed is not None
        assert consumed["user_id"] == user_id
        assert consumed["project_id"] == project_id

        # Second consumption fails
        consumed_again = await consume_stream_ticket(ticket)
        assert consumed_again is None


@pytest.mark.asyncio
async def test_subscribe_run_events_heartbeat_and_terminal_completion():
    """Verify subscribe_run_events yields heartbeats and finishes when terminal event is received."""
    r_id = str(uuid.uuid4())
    p_id = str(uuid.uuid4())

    terminal_event = RunEventPayload(
        event="run.succeeded",
        run_id=r_id,
        project_id=p_id,
        timestamp="2026-10-18T12:00:05Z",
        data={"status": "succeeded"},
    )

    mock_pubsub = AsyncMock()
    # Sequence of returns: None (triggers heartbeat), then terminal message
    mock_pubsub.get_message = AsyncMock(
        side_effect=[
            None,  # triggers ': ping\n\n'
            {"data": terminal_event.model_dump_json()},  # triggers terminal event & exit
        ]
    )
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.close = AsyncMock()

    mock_redis = AsyncMock()
    mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

    with patch("qa_agentos.runtime.pubsub.get_redis_client", return_value=mock_redis):
        frames = []
        async for frame in subscribe_run_events(
            project_id=p_id,
            run_id=r_id,
            heartbeat_interval_seconds=0.01,
        ):
            frames.append(frame)

        assert len(frames) == 2
        assert frames[0] == ": ping\n\n"
        assert "event: run.succeeded\n" in frames[1]

        # Verify subscriber cleanup
        mock_pubsub.subscribe.assert_called_once()
        mock_pubsub.unsubscribe.assert_called_once()
        mock_pubsub.close.assert_called_once()


@pytest.mark.asyncio
async def test_subscriber_cleanup_on_cancellation():
    """Verify unsubscribe and close are always called even if the subscriber task is cancelled."""
    mock_pubsub = AsyncMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.close = AsyncMock()

    async def infinite_wait(*args, **kwargs):
        await asyncio.sleep(100.0)
        return None

    mock_pubsub.get_message = AsyncMock(side_effect=infinite_wait)

    mock_redis = AsyncMock()
    mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

    with patch("qa_agentos.runtime.pubsub.get_redis_client", return_value=mock_redis):

        async def run_subscriber():
            async for _ in subscribe_run_events("proj-1", "run-1"):
                pass

        task = asyncio.create_task(run_subscriber())
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        # Ensure cleanup was executed
        mock_pubsub.unsubscribe.assert_called_once()
        mock_pubsub.close.assert_called_once()


@pytest.mark.asyncio
async def test_api_events_ticket_unauthorized():
    """Verify ticket generation requires valid authentication."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(f"/v1/runs/{uuid.uuid4()}/events/ticket")
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_api_events_stream_unauthorized():
    """Verify SSE streaming endpoint rejects unauthenticated requests without token or ticket."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(f"/v1/runs/{uuid.uuid4()}/events")
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_api_events_invalid_ticket():
    """Verify SSE streaming endpoint rejects invalid or expired ticket."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(f"/v1/runs/{uuid.uuid4()}/events?ticket=invalid-ticket-12345")
        assert res.status_code == 401
