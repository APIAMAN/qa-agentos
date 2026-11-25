"""Response schemas for exported report versions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict
from qa_agentos.core.config import settings
from qa_agentos.models.domain import Report


class ReportArtifactRef(BaseModel):
    id: uuid.UUID
    href: str


class ReportVersionResponse(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    project_id: uuid.UUID
    version: int
    schema_version: str
    verdict: str
    content_hash: str
    json_artifact: Optional[ReportArtifactRef] = None
    html_artifact: Optional[ReportArtifactRef] = None
    created_at: datetime
    #: Stable console URL of the run's report (latest version is shown by default).
    report_url: str

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_row(cls, row: Report) -> "ReportVersionResponse":
        def ref(art_id: Optional[uuid.UUID]) -> Optional[ReportArtifactRef]:
            return ReportArtifactRef(id=art_id, href=f"{settings.API_V1_STR}/artifacts/{art_id}") if art_id else None

        return cls(
            id=row.id,
            run_id=row.run_id,
            project_id=row.project_id,
            version=row.version,
            schema_version=row.schema_version,
            verdict=row.verdict,
            content_hash=row.content_hash,
            json_artifact=ref(row.json_artifact_id),
            html_artifact=ref(row.html_artifact_id),
            created_at=row.created_at,
            report_url=f"/runs/{row.run_id}/report?version={row.version}",
        )
