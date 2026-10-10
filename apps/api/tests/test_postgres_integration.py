from qa_agentos.models.domain import Project
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def test_postgres_session_crud_and_rollback(pg_session: AsyncSession):
    """Verify basic PostgreSQL CRUD and transaction isolation fixture."""
    proj = Project(name="Postgres Test Project", slug="pg-test-project")
    pg_session.add(proj)
    await pg_session.commit()

    result = await pg_session.execute(select(Project).where(Project.slug == "pg-test-project"))
    fetched = result.scalar_one_or_none()
    assert fetched is not None
    assert fetched.name == "Postgres Test Project"
