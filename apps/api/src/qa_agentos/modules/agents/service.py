"""Agents service for catalog retrieval."""

from __future__ import annotations

from typing import List, Optional

from qa_agentos.core.errors import NotFoundError
from qa_agentos.models.domain import AgentDefinition, AgentVersion
from qa_agentos.modules.agents.schemas import (
    AgentDetailResponse,
    AgentListItem,
    AgentVersionResponse,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


class AgentsService:
    @staticmethod
    def _select_active_version(versions: List[AgentVersion]) -> Optional[AgentVersion]:
        """Find the active version among agent versions, or fallback to the most recent."""
        active = [v for v in versions if v.status == "active"]
        if active:
            active.sort(key=lambda v: v.activated_at or v.created_at, reverse=True)
            return active[0]
        if versions:
            sorted_vers = sorted(versions, key=lambda v: v.created_at, reverse=True)
            return sorted_vers[0]
        return None

    @classmethod
    async def list_agents(
        cls,
        db: AsyncSession,
        include_inactive: bool = False,
    ) -> List[AgentListItem]:
        stmt = (
            select(AgentDefinition).options(selectinload(AgentDefinition.versions)).order_by(AgentDefinition.name.asc())
        )
        if not include_inactive:
            stmt = stmt.where(AgentDefinition.is_active.is_(True))

        result = await db.execute(stmt)
        agents = result.scalars().all()

        items: List[AgentListItem] = []
        for agent in agents:
            versions = agent.versions or []
            active_ver = cls._select_active_version(versions)
            version_str = active_ver.version if active_ver else None
            status_str = active_ver.status if active_ver else ("inactive" if not agent.is_active else "draft")

            items.append(
                AgentListItem(
                    id=agent.id,
                    slug=agent.slug,
                    name=agent.name,
                    description=agent.description,
                    agent_type=agent.agent_type,
                    is_active=agent.is_active,
                    created_at=agent.created_at,
                    version=version_str,
                    status=status_str,
                    input_schema=active_ver.input_schema if active_ver else None,
                    output_schema=active_ver.output_schema if active_ver else None,
                    parameters=active_ver.parameters if active_ver else None,
                    permissions=active_ver.permissions if active_ver else [],
                    allowed_tools=active_ver.allowed_tools if active_ver else [],
                    model_requirements=active_ver.model_requirements if active_ver else None,
                    available_versions=[v.version for v in versions],
                )
            )

        return items

    @classmethod
    async def get_agent_by_slug(
        cls,
        db: AsyncSession,
        slug: str,
        include_inactive: bool = False,
    ) -> AgentDetailResponse:
        stmt = (
            select(AgentDefinition).where(AgentDefinition.slug == slug).options(selectinload(AgentDefinition.versions))
        )
        result = await db.execute(stmt)
        agent = result.scalar_one_or_none()

        if not agent:
            raise NotFoundError(message=f"Agent '{slug}' not found")

        if not agent.is_active and not include_inactive:
            raise NotFoundError(message=f"Agent '{slug}' is inactive or not found")

        versions = agent.versions or []
        sorted_versions = sorted(versions, key=lambda v: (v.status == "active", v.created_at), reverse=True)
        active_ver = cls._select_active_version(versions)

        return AgentDetailResponse(
            id=agent.id,
            slug=agent.slug,
            name=agent.name,
            description=agent.description,
            agent_type=agent.agent_type,
            is_active=agent.is_active,
            created_at=agent.created_at,
            version=active_ver.version if active_ver else None,
            status=active_ver.status if active_ver else ("inactive" if not agent.is_active else "draft"),
            input_schema=active_ver.input_schema if active_ver else None,
            output_schema=active_ver.output_schema if active_ver else None,
            parameters=active_ver.parameters if active_ver else None,
            permissions=active_ver.permissions if active_ver else [],
            allowed_tools=active_ver.allowed_tools if active_ver else [],
            model_requirements=active_ver.model_requirements if active_ver else None,
            active_version=AgentVersionResponse.model_validate(active_ver) if active_ver else None,
            versions=[AgentVersionResponse.model_validate(v) for v in sorted_versions],
        )
