"""Default output sink for succeeded runs: the output artifact plus declared post-processing.

An agent version opts into post-processing with ``parameters.output_sinks`` (e.g. the Bug
Analyzer declares ``[bug_store]``). Every step runs in its own savepoint inside the run's
transaction and never raises: the outcome of each step is returned and recorded on the run's
``succeeded`` event (``artifact_status``, ``bug_status`` …), so a failure is visible, not hidden,
and one failing step cannot undo the run or the other steps.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import structlog
from qa_agentos.core.config import settings
from qa_agentos.models.domain import AgentVersion, Run
from qa_agentos.modules.artifacts.service import store_run_output_artifact
from qa_agentos.modules.bugs.service import BugStore
from qa_agentos.runtime.executor import OutputSink
from qa_agentos.runtime.gateway.embeddings import Embedder, embedder_from_settings
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

EmbedderFactory = Callable[[], Optional[Embedder]]


def _artifact_name(run: Run) -> Optional[str]:
    slug = (run.resolved_agent or {}).get("slug")
    if not slug or slug == "test-case-generator":
        return None  # keep the established "testcases-<id>.json" name
    return f"{slug}-{str(run.id)[:8]}.json"


def make_run_output_sink(
    embedder_factory: Optional[EmbedderFactory] = None, *, duplicate_threshold: Optional[float] = None
) -> OutputSink:
    def default_embedder() -> Optional[Embedder]:
        return embedder_from_settings(settings)

    make_embedder = embedder_factory or default_embedder

    async def run_output_sink(db: AsyncSession, run: Run, output: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        extra: Dict[str, Any] = {}
        with db.no_autoflush:  # see BugStore.record_from_run: keep the run row unlocked until the embedding is done
            version = await db.get(AgentVersion, run.agent_version_id)
        sinks = list(((version.parameters or {}) if version else {}).get("output_sinks") or [])
        if "bug_store" in sinks:
            embedder = None
            try:
                embedder = make_embedder()
                store = BugStore(
                    embedder,
                    threshold=duplicate_threshold if duplicate_threshold is not None else settings.DUPLICATE_SIMILARITY_THRESHOLD,
                )
                prepared = await store.prepare(db, run, output)  # outside the savepoint: no row lock during the call
                if prepared is None:  # already recorded for this run (idempotent)
                    bug = await store.record_from_run(db, run, output)
                else:
                    async with db.begin_nested():
                        bug = await store.record(db, run, output, prepared)
                extra.update(
                    bug_status="stored",
                    bug_id=str(bug.id),
                    bug_cluster_id=str(bug.cluster_id),
                    bug_match=bug.match.get("method"),
                    bug_grounded=bug.grounded,
                )
            except Exception as exc:
                logger.error("Failed to record bug report", run_id=str(run.id), error=type(exc).__name__)
                extra.update(bug_status="failed", bug_error=type(exc).__name__)
            finally:
                if embedder is not None:
                    await embedder.aclose()

        try:
            async with db.begin_nested():
                await store_run_output_artifact(db, run, output, name=_artifact_name(run))
        except Exception as exc:
            logger.error("Failed to store run output artifact", run_id=str(run.id), error=type(exc).__name__)
            extra.update(artifact_status="failed", artifact_error=type(exc).__name__)
        return extra

    return run_output_sink


#: Default sink used by the worker and the inline dispatcher.
run_output_sink: OutputSink = make_run_output_sink()
