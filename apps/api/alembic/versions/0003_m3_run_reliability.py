"""M3 run reliability: attempt fencing, resume intent, heartbeats, per-attempt step history

Revision ID: 0003_m3_run_reliability
Revises: 0002_m1_run_engine
Create Date: 2026-11-24

Backward compatible: every new column is nullable, so existing rows and older
application code keep working. ``run_steps`` uniqueness widens from
``(run_id, step_number)`` to ``(run_id, step_number, attempt)`` so a retried
attempt records its own row instead of overwriting the previous attempt's.

Downgrade note: restoring the narrower unique constraint fails if a run already
has more than one attempt recorded for the same step number. Remove the older
attempt rows deliberately before downgrading; the migration does not delete data.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_m3_run_reliability"
down_revision: Union[str, None] = "0002_m1_run_engine"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Set by an approval decision; consumed exactly once by the worker that resumes the run.
    op.add_column("runs", sa.Column("resume_requested_at", sa.DateTime(timezone=True), nullable=True))
    # Hatchet-local attempt number of the delivery that currently owns the run.
    op.add_column("runs", sa.Column("hatchet_attempt", sa.Integer(), nullable=True))
    # Refreshed at claim time and after every graph superstep; drives stale-run recovery.
    op.add_column("runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_runs_status_heartbeat_at", "runs", ["status", "heartbeat_at"])

    op.drop_constraint("uq_run_steps_run_id_step_number", "run_steps", type_="unique")
    op.create_unique_constraint(
        "uq_run_steps_run_id_step_number_attempt",
        "run_steps",
        ["run_id", "step_number", "attempt"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_run_steps_run_id_step_number_attempt", "run_steps", type_="unique")
    op.create_unique_constraint("uq_run_steps_run_id_step_number", "run_steps", ["run_id", "step_number"])

    op.drop_index("ix_runs_status_heartbeat_at", table_name="runs")
    op.drop_column("runs", "heartbeat_at")
    op.drop_column("runs", "hatchet_attempt")
    op.drop_column("runs", "resume_requested_at")
