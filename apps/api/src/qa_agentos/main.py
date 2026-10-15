from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from qa_agentos.core.config import settings
from qa_agentos.core.errors import APIError, api_error_handler
from qa_agentos.core.logging import logger, setup_logging
from qa_agentos.modules.auth.router import router as auth_router
from qa_agentos.modules.projects.router import router as projects_router
from qa_agentos.modules.runs.dispatcher import get_dispatcher, set_dispatcher
from qa_agentos.modules.runs.router import router as runs_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    setup_logging()
    logger.info(
        "QA AgentOS Control Plane starting up", environment=settings.ENVIRONMENT, dispatcher=settings.RUN_DISPATCHER
    )
    dispatcher = get_dispatcher()
    set_dispatcher(dispatcher)
    yield
    logger.info("QA AgentOS Control Plane shutting down")
    await dispatcher.shutdown()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    description="QA AgentOS Modular Monolith Control Plane API",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(APIError, api_error_handler)  # type: ignore[arg-type]

# Mount domain routers under /v1
app.include_router(auth_router, prefix=settings.API_V1_STR)
app.include_router(projects_router, prefix=settings.API_V1_STR)
app.include_router(runs_router, prefix=settings.API_V1_STR)


@app.get("/health", tags=["Health"])
async def health_check() -> Dict[str, str]:
    return {
        "status": "healthy",
        "service": "qa-agentos-api",
        "environment": settings.ENVIRONMENT,
        "dispatcher": settings.RUN_DISPATCHER,
    }
