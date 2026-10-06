import uuid
from typing import Any, Callable, Coroutine, Optional

from fastapi import Depends, Header
from fastapi.security import OAuth2PasswordBearer
from qa_agentos.core.database import get_db
from qa_agentos.core.errors import ForbiddenError, UnauthorizedError
from qa_agentos.core.security import (
    decode_access_token,
    hash_api_key,
    verify_password,
)
from qa_agentos.models.domain import APIKey, User
from qa_agentos.modules.auth.schemas import Identity
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/token", auto_error=False)


async def authenticate_user(db: AsyncSession, email: str, password: str) -> Optional[User]:
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


async def get_current_identity(
    bearer_token: Optional[str] = Depends(oauth2_scheme),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> Identity:
    if bearer_token:
        payload = decode_access_token(bearer_token)
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise UnauthorizedError(message="Invalid token claims")
        user_id = uuid.UUID(str(user_id_str))
        stmt_user = select(User).where(User.id == user_id)
        res_user = await db.execute(stmt_user)
        user = res_user.scalar_one_or_none()
        if not user or not user.is_active:
            raise UnauthorizedError(message="User account inactive or not found")
        return Identity(
            user_id=user.id,
            email=user.email,
            role=user.role,
            is_api_key=False,
            scopes=["*"],
        )

    if x_api_key:
        hashed_key = hash_api_key(x_api_key)
        stmt_key = select(APIKey).where(APIKey.hashed_key == hashed_key)
        res_key = await db.execute(stmt_key)
        api_key = res_key.scalar_one_or_none()
        if not api_key:
            raise UnauthorizedError(message="Invalid API Key")
        return Identity(
            project_id=api_key.project_id,
            api_key_id=api_key.id,
            scopes=api_key.scopes,
            is_api_key=True,
        )

    raise UnauthorizedError(message="Authentication credentials required (Bearer token or X-API-Key header)")


def require_scope(required_scope: str) -> Callable[[Identity], Coroutine[Any, Any, Identity]]:
    async def scope_checker(identity: Identity = Depends(get_current_identity)) -> Identity:
        if identity.is_api_key:
            if "*" in identity.scopes or required_scope in identity.scopes:
                return identity
            raise ForbiddenError(message=f"API Key lacks required scope: {required_scope}")
        return identity
    return scope_checker
