import asyncio
from typing import Optional

from qa_agentos.core.database import AsyncSessionLocal
from qa_agentos.core.security import hash_password
from qa_agentos.models.domain import Project, User
from qa_agentos.registry.sync import sync_registries
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def _run_bootstrap(db: AsyncSession) -> None:
    # 1. Seed Demo User
    stmt_user = select(User).where(User.email == "admin@qa-agentos.local")
    res_user = await db.execute(stmt_user)
    user = res_user.scalar_one_or_none()
    if not user:
        user = User(
            email="admin@qa-agentos.local",
            hashed_password=hash_password("admin123456"),
            full_name="QA AgentOS Admin",
            role="owner",
        )
        db.add(user)
        await db.commit()
        print("Seeded admin user: admin@qa-agentos.local / admin123456")
    else:
        print("Admin user already exists")

    # 2. Seed Demo Project
    stmt_proj = select(Project).where(Project.slug == "demo-project")
    res_proj = await db.execute(stmt_proj)
    project = res_proj.scalar_one_or_none()
    if not project:
        project = Project(
            name="Demo Project",
            slug="demo-project",
            description="Default seeded demonstration project for QA AgentOS",
        )
        db.add(project)
        await db.commit()
        print("Seeded demo project: demo-project")
    else:
        print("Demo project already exists")

    # 3. Synchronize Registries
    sync_result = await sync_registries(db)
    print(
        f"Synchronized registries: "
        f"models (+{sync_result.models_created}/~{sync_result.models_updated}), "
        f"tools (+{sync_result.tools_created}/~{sync_result.tools_updated}), "
        f"agents (+{sync_result.agents_created}/~{sync_result.agents_updated}), "
        f"versions (+{sync_result.versions_created}/~{sync_result.versions_updated})"
    )


async def bootstrap_demo_data(db: Optional[AsyncSession] = None) -> None:
    """Seed default admin user, demo project, and synchronize registries idempotently."""
    if db is not None:
        await _run_bootstrap(db)
    else:
        async with AsyncSessionLocal() as session:
            await _run_bootstrap(session)


if __name__ == "__main__":
    asyncio.run(bootstrap_demo_data())
