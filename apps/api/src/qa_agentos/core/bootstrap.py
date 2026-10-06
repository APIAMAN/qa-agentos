import asyncio

from qa_agentos.core.database import AsyncSessionLocal
from qa_agentos.core.security import hash_password
from qa_agentos.models.domain import Project, User
from sqlalchemy import select


async def bootstrap_demo_data() -> None:
    async with AsyncSessionLocal() as db:
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


if __name__ == "__main__":
    asyncio.run(bootstrap_demo_data())
