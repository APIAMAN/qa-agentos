"""FastAPI router for run reports."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from qa_agentos.core.database import get_db
from qa_agentos.modules.auth.schemas import Identity
from qa_agentos.modules.auth.service import require_scope
from qa_agentos.modules.reports.schemas import ReportVersionResponse
from qa_agentos.modules.reports.service import ReportsService
from qa_agentos.modules.runs.service import resolve_project_id
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/runs", tags=["Reports"])


@router.get("/{run_id}/report")
async def get_run_report(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("runs:read")),
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
) -> Dict[str, Any]:
    """Schema-validated JSON report of a run (summary, verdict, timelines, usage, approvals, artifacts)."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    return await ReportsService.build_run_report(db, run_id, project_id)


@router.post(
    "/{run_id}/report:export",
    response_model=ReportVersionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "Unchanged evidence: the existing version is returned"}},
)
async def export_run_report(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("artifacts:write")),
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
) -> JSONResponse:
    """Export the report as a new immutable version (JSON + HTML artifacts); idempotent per content."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    row, created = await ReportsService.export_run_report(db, run_id, project_id, identity)
    body = jsonable_encoder(ReportVersionResponse.from_row(row))
    return JSONResponse(status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK, content=body)


@router.get("/{run_id}/reports", response_model=List[ReportVersionResponse])
async def list_run_report_versions(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("runs:read")),
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
) -> List[ReportVersionResponse]:
    """Exported report versions of a run, oldest first."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    rows = await ReportsService.list_report_versions(db, run_id, project_id)
    return [ReportVersionResponse.from_row(r) for r in rows]


@router.get("/{run_id}/reports/{version}", response_model=ReportVersionResponse)
async def get_run_report_version(
    run_id: uuid.UUID,
    version: int,
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("runs:read")),
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
) -> ReportVersionResponse:
    """One exported report version (metadata and artifact references)."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    return ReportVersionResponse.from_row(await ReportsService.get_report_version(db, run_id, project_id, version))
