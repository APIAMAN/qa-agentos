"""FastAPI router for QA AgentOS Agents Catalog API."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query
from qa_agentos.core.database import get_db
from qa_agentos.modules.agents.schemas import AgentDetailResponse, AgentListItem
from qa_agentos.modules.agents.service import AgentsService
from qa_agentos.modules.auth.schemas import Identity
from qa_agentos.modules.auth.service import require_scope
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/agents", tags=["Agents"])


@router.get("", response_model=List[AgentListItem])
async def list_agents(
    include_inactive: bool = Query(False, description="Include inactive agent definitions"),
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("agents:read")),
) -> List[AgentListItem]:
    """Retrieve all available agent definitions from the catalog."""
    return await AgentsService.list_agents(db=db, include_inactive=include_inactive)


@router.get("/{slug}", response_model=AgentDetailResponse)
async def get_agent(
    slug: str,
    include_inactive: bool = Query(False, description="Include inactive agent definitions"),
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("agents:read")),
) -> AgentDetailResponse:
    """Retrieve detailed specification and schemas for a specific agent by slug."""
    return await AgentsService.get_agent_by_slug(db=db, slug=slug, include_inactive=include_inactive)
