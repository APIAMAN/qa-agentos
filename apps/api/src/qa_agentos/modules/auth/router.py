from fastapi import APIRouter, Depends
from qa_agentos.core.database import get_db
from qa_agentos.core.errors import UnauthorizedError
from qa_agentos.core.security import create_access_token
from qa_agentos.models.domain import User
from qa_agentos.modules.auth.schemas import Identity, LoginRequest, Token, UserResponse
from qa_agentos.modules.auth.service import authenticate_user, get_current_identity
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/token", response_model=Token)
async def login_for_access_token(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> Token:
    user = await authenticate_user(db, payload.email, payload.password)
    if not user:
        raise UnauthorizedError(message="Incorrect email or password")
    access_token = create_access_token(subject=str(user.id), claims={"role": user.role, "email": user.email})
    return Token(access_token=access_token)


@router.get("/me", response_model=UserResponse)
async def get_me(
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    if identity.is_api_key or not identity.user_id:
        raise UnauthorizedError(message="Endpoint requires User session token")
    stmt = select(User).where(User.id == identity.user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise UnauthorizedError(message="User not found")
    return UserResponse.model_validate(user)
