"""Redis Pub/Sub and Server-Sent Events (SSE) streaming infrastructure for QA AgentOS.

Provides versioned run event schema, Redis publishing/subscribing,
stream ticket generation/consumption, and SSE frame formatting.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional, Tuple, cast

import redis.asyncio as aioredis
from pydantic import BaseModel, Field
from structlog import get_logger

from qa_agentos.core.config import settings

logger = get_logger(__name__)

# Fallback in-memory storage for tickets if Redis is unavailable: {ticket: (payload, expire_at)}
_MEMORY_TICKETS: Dict[str, Tuple[Dict[str, str], float]] = {}


def get_run_events_channel(project_id: str | uuid.UUID, run_id: str | uuid.UUID) -> str:
    """Return the scoped Redis Pub/Sub channel for run events."""
    return f"projects:{project_id}:runs:{run_id}:events"


_TERMINAL_EVENTS = ("run.succeeded", "run.failed", "run.cancelled")


def _is_terminal_frame(frame: str) -> bool:
    return any(f"event: {name}\n" in frame for name in _TERMINAL_EVENTS)


class RunEventPayload(BaseModel):
    """Versioned schema for run and step lifecycle events."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    version: str = "1.0"
    event: str  # e.g., "run.queued", "run.running", "run.succeeded", "step.completed"
    run_id: str
    project_id: str
    timestamp: str
    data: Dict[str, Any] = Field(default_factory=dict)

    def format_sse(self) -> str:
        """Format event as an SSE message frame with id, event, and data lines."""
        payload_json = json.dumps(self.model_dump(), default=str)
        return f"id: {self.id}\nevent: {self.event}\ndata: {payload_json}\n\n"


_redis_pool: Optional[aioredis.ConnectionPool] = None
_redis_client: Optional[aioredis.Redis] = None
_client_loop: Optional[asyncio.AbstractEventLoop] = None


async def get_redis_client() -> aioredis.Redis:
    """Return an async Redis client, initializing connection pool if necessary."""
    global _redis_pool, _redis_client, _client_loop
    current_loop = asyncio.get_running_loop()
    if _redis_client is None or _client_loop != current_loop or current_loop.is_closed():
        _client_loop = current_loop
        url = settings.REDIS_URL
        # Local-dev convenience only (Windows/WSL port forwarder). Never in production, where it
        # could silently redirect event traffic to whatever listens on localhost:6381.
        if ":6379" in url and settings.ENVIRONMENT != "production":
            import socket
            sock = socket.socket()
            sock.settimeout(0.2)
            try:
                # If forwarder 6381 is available, prefer 6381 over broken/blocked 6379
                if sock.connect_ex(("localhost", 6381)) == 0:
                    url = url.replace(":6379", ":6381")
            except Exception:
                pass
            finally:
                sock.close()
        _redis_pool = aioredis.ConnectionPool.from_url(
            url,
            decode_responses=True,
            max_connections=20,
            socket_timeout=3.0,
            socket_connect_timeout=3.0,
        )
        _redis_client = aioredis.Redis(connection_pool=_redis_pool)
    return _redis_client


async def close_redis() -> None:
    """Gracefully close Redis client and connection pool."""
    global _redis_pool, _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.aclose()
        except Exception:
            pass
        _redis_client = None
    if _redis_pool is not None:
        try:
            await _redis_pool.disconnect()
        except Exception:
            pass
        _redis_pool = None


async def publish_run_event(event: RunEventPayload) -> bool:
    """Publish a run event to the scoped Redis channel.

    Safe against Redis downtime; returns True on success, False on failure without raising.
    """
    channel = get_run_events_channel(event.project_id, event.run_id)
    payload_json = json.dumps(event.model_dump(), default=str)
    try:
        r = await get_redis_client()
        await r.publish(channel, payload_json)
        logger.debug(
            "Published run event",
            event_type=event.event,
            run_id=event.run_id,
            channel=channel,
        )
        return True
    except Exception as exc:
        logger.warning(
            "Failed to publish run event to Redis (non-fatal)",
            event_type=event.event,
            run_id=event.run_id,
            error=str(exc),
        )
        return False


async def create_stream_ticket(
    user_id: str,
    project_id: str,
    run_id: Optional[str] = None,
    ttl_seconds: int = 60,
) -> str:
    """Generate a cryptographically random, short-lived single-use ticket for SSE connection."""
    ticket = secrets.token_urlsafe(32)
    ticket_payload = {"user_id": str(user_id), "project_id": str(project_id)}
    if run_id:
        ticket_payload["run_id"] = str(run_id)

    saved_in_redis = False
    try:
        r = await get_redis_client()
        key = f"stream_ticket:{ticket}"
        await r.setex(key, ttl_seconds, json.dumps(ticket_payload))
        saved_in_redis = True
    except Exception as exc:
        logger.warning("Failed to store stream ticket in Redis; using in-memory store", error=str(exc))

    if not saved_in_redis:
        # Save in memory fallback with expiration
        expire_at = time.time() + ttl_seconds
        _MEMORY_TICKETS[ticket] = (ticket_payload, expire_at)
        # Prune expired memory tickets
        now = time.time()
        expired_keys = [k for k, v in _MEMORY_TICKETS.items() if v[1] < now]
        for k in expired_keys:
            _MEMORY_TICKETS.pop(k, None)

    return ticket


async def consume_stream_ticket(ticket: str) -> Optional[Dict[str, str]]:
    """Validate and consume a single-use stream ticket. Returns ticket payload if valid."""
    if not ticket:
        return None

    # Check Redis first
    try:
        r = await get_redis_client()
        key = f"stream_ticket:{ticket}"
        raw = await r.getdel(key)  # Atomic single-use consumption (no get/delete race)
        if raw:
            return cast(Optional[dict[str, str]], json.loads(raw))
    except Exception as exc:
        logger.warning("Failed to consume ticket from Redis; checking memory store", error=str(exc))

    # Check in-memory fallback
    now = time.time()
    if ticket in _MEMORY_TICKETS:
        data, expire_at = _MEMORY_TICKETS.pop(ticket)
        if expire_at >= now:
            return data

    return None


async def subscribe_run_events(
    project_id: str | uuid.UUID,
    run_id: str | uuid.UUID,
    *,
    heartbeat_interval_seconds: float = 15.0,
    after_subscribe: Optional[Callable[[], Awaitable[List[str]]]] = None,
) -> AsyncGenerator[str, None]:
    """Async generator yielding SSE formatted event strings and heartbeats from Redis Pub/Sub.

    ``after_subscribe`` runs once the subscription is active and returns frames to emit
    first (e.g. a fresh database snapshot). Subscribing *before* reading state means an
    event published in between is buffered rather than lost. If a returned frame is a
    terminal run event, the stream ends after emitting it.
    """
    channel = get_run_events_channel(project_id, run_id)
    r = await get_redis_client()
    pubsub = r.pubsub()

    try:
        await pubsub.subscribe(channel)
        logger.info("Subscribed to run events channel", channel=channel)

        if after_subscribe is not None:
            initial_frames = await after_subscribe()
            for frame in initial_frames:
                yield frame
            if any(_is_terminal_frame(f) for f in initial_frames):
                return

        while True:
            try:
                # Wait for next message with a timeout matching the heartbeat interval
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=heartbeat_interval_seconds,
                )
                if message is not None:
                    raw_data = message.get("data")
                    if raw_data:
                        try:
                            parsed = json.loads(raw_data)
                            event_payload = RunEventPayload.model_validate(parsed)
                            yield event_payload.format_sse()
                            # Check if terminal run event was published
                            if event_payload.event in ("run.succeeded", "run.failed", "run.cancelled"):
                                logger.info(
                                    "Terminal event received on channel; completing stream",
                                    event_type=event_payload.event,
                                    channel=channel,
                                )
                                break
                        except Exception as parse_err:
                            logger.warning("Invalid event payload received on channel", error=str(parse_err))
                else:
                    # Heartbeat comment to keep HTTP connection alive and detect browser disconnect
                    yield ": ping\n\n"
            except asyncio.CancelledError:
                logger.info("SSE subscription stream cancelled by client", channel=channel)
                break
            except Exception as stream_exc:
                logger.warning("Error in SSE event stream", error=str(stream_exc), channel=channel)
                # Yield error comment or break
                yield f": error {str(stream_exc)}\n\n"
                break
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
            logger.info("Unsubscribed and cleaned up run events channel", channel=channel)
        except Exception as cleanup_err:
            logger.warning("Error cleaning up Redis pubsub subscription", error=str(cleanup_err))
