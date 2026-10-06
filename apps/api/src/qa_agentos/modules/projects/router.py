import secrets
import uuid
from typing import List

from fastapi import APIRouter, Depends, status
from qa_agentos.core.database import get_db
from qa_agentos.core.errors import NotFoundError
from qa_agentos.core.security import hash_api_key
from qa_agentos.models.domain import APIKey, Project
from qa_agentos.modules.auth.schemas import Identity
from qa_agentos.modules.auth.service import get_current_identity, require_scope
from qa_agentos.modules.projects.schemas import APIKeyCreate, APIKeyResponse, ProjectCreate, ProjectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(get_current_identity),
) -> ProjectResponse:
    project = Project(
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return ProjectResponse.model_validate(project)


@router.get("", response_model=List[ProjectResponse])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("projects:read")),
) -> List[ProjectResponse]:
    if identity.is_api_key and identity.project_id:
        stmt = select(Project).where(Project.id == identity.project_id)
    else:
        stmt = select(Project)
    res = await db.execute(stmt)
    projects = res.scalars().all()
    return [ProjectResponse.model_validate(p) for p in projects]


@router.post("/{project_id}/keys", response_model=APIKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_project_api_key(
    project_id: uuid.UUID,
    payload: APIKeyCreate,
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(get_current_identity),
) -> APIKeyResponse:
    stmt = select(Project).where(Project.id == project_id)
    res = await db.execute(stmt)
    project = res.scalar_one_or_none()
    if not project:
        raise NotFoundError(message="Project not found")

    raw_secret = secrets.token_urlsafe(32)
    key_prefix = raw_secret[:8]
    raw_key = f"qakey_{raw_secret}"
    hashed_key = hash_api_key(raw_key)

    api_key_obj = APIKey(
        project_id=project.id,
        name=payload.name,
        key_prefix=key_prefix,
        hashed_key=hashed_key,
        scopes=payload.scopes,
    )
    db.add(api_key_obj)
    await db.commit()
    await db.refresh(api_key_obj)

    response_data = APIKeyResponse.model_validate(api_key_obj)
    response_data.raw_key = raw_key
    return response_data
