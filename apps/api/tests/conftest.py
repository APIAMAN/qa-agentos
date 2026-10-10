import asyncio
import os
import pathlib
from typing import AsyncGenerator

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from qa_agentos.core.database import Base, get_db
from qa_agentos.main import app
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# SQLite in-memory for fast unit testing
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestingSessionLocal() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


# ============================================================================
# Dedicated PostgreSQL Integration Test Infrastructure
# ============================================================================

def get_postgres_test_url() -> str:
    """Retrieve and validate the dedicated PostgreSQL test database URL.

    - In CI: Fails loudly if TEST_DATABASE_URL is missing.
    - Locally: Skips gracefully with a clear message if TEST_DATABASE_URL is not set.
    - Safety guard: Prevents destructive operations by verifying that the database name
      contains 'test' (e.g. qa_agentos_test).
    """
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        if os.getenv("CI") or os.getenv("GITHUB_ACTIONS"):
            raise RuntimeError(
                "TEST_DATABASE_URL environment variable must be set in CI for PostgreSQL integration tests."
            )
        pytest.skip("TEST_DATABASE_URL is not set; skipping PostgreSQL integration tests.")

    parsed = make_url(url)
    db_name = (parsed.database or "").lower()
    if not ("test" in db_name or "disposable" in db_name or os.getenv("ALLOW_DESTRUCTIVE_TEST_DB") == "true"):
        raise ValueError(
            f"Unsafe TEST_DATABASE_URL: database name '{parsed.database}' does not contain 'test'. "
            "To prevent data loss, integration tests must target a dedicated disposable database (e.g. qa_agentos_test)."
        )

    return url


@pytest.fixture(scope="session")
def postgres_test_url() -> str:
    """Session-scoped fixture providing the validated PostgreSQL test URL."""
    return get_postgres_test_url()


@pytest.fixture(scope="session")
async def pg_engine(postgres_test_url: str) -> AsyncGenerator[AsyncEngine, None]:
    """Session-scoped PostgreSQL async engine for integration tests.

    Ensures the target database exists, applies Alembic migrations to head,
    and disposes the engine on session teardown.
    """
    url_obj = make_url(postgres_test_url)
    db_name = url_obj.database
    assert db_name, "TEST_DATABASE_URL must specify a database name"

    # Step 1: Ensure database exists by connecting to an admin database if needed
    admin_candidates = ["postgres", "qa_agentos", "template1"]
    if os.getenv("DATABASE_URL"):
        try:
            cand = make_url(os.getenv("DATABASE_URL")).database
            if cand and cand not in admin_candidates:
                admin_candidates.append(cand)
        except Exception:
            pass

    for admin_db in admin_candidates:
        try:
            conn = await asyncpg.connect(
                user=url_obj.username or "postgres",
                password=url_obj.password or "postgres",
                host=url_obj.host or "localhost",
                port=url_obj.port or 5432,
                database=admin_db,
            )
            try:
                exists = await conn.fetchval(
                    "SELECT 1 FROM pg_database WHERE datname = $1", db_name
                )
                if not exists:
                    await conn.execute(f'CREATE DATABASE "{db_name}"')
                break
            finally:
                await conn.close()
        except Exception:
            continue

    # Step 2: Run Alembic migrations up to head on the test database
    api_dir = pathlib.Path(__file__).resolve().parent.parent
    alembic_ini_path = api_dir / "alembic.ini"
    alembic_cfg = Config(str(alembic_ini_path))
    alembic_cfg.set_main_option("script_location", str(api_dir / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", postgres_test_url)
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")

    # Step 3: Yield test engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(postgres_test_url, echo=False, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def pg_session(pg_engine) -> AsyncGenerator[AsyncSession, None]:
    """Test-scoped PostgreSQL session with transaction-level isolation."""
    async with pg_engine.connect() as conn:
        trans = await conn.begin()
        session_factory = async_sessionmaker(
            conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
            class_=AsyncSession,
        )
        async with session_factory() as session:
            yield session
        await trans.rollback()


@pytest.fixture
async def pg_client(pg_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient configured with dependency overrides to use the PostgreSQL test session."""
    async def _override_get_db():
        yield pg_session

    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
