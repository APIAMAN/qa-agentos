import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from passlib.context import CryptContext
from qa_agentos.core.config import settings
from qa_agentos.core.errors import UnauthorizedError

pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")

_RSA_KEY_PAIR: Optional[Tuple[bytes, bytes]] = None


def get_rsa_keys() -> Tuple[bytes, bytes]:
    global _RSA_KEY_PAIR
    if settings.RSA_PRIVATE_KEY_PEM and settings.RSA_PUBLIC_KEY_PEM:
        return (
            settings.RSA_PRIVATE_KEY_PEM.encode("utf-8"),
            settings.RSA_PUBLIC_KEY_PEM.encode("utf-8"),
        )
    if _RSA_KEY_PAIR is None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        _RSA_KEY_PAIR = (private_pem, public_pem)
    return _RSA_KEY_PAIR


def hash_password(password: str) -> str:
    return str(pwd_context.hash(password))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bool(pwd_context.verify(plain_password, hashed_password))


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def create_access_token(
    subject: str,
    expires_delta: Optional[timedelta] = None,
    claims: Optional[Dict[str, Any]] = None,
) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode = {
        "sub": subject,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "iss": "qa-agentos",
    }
    if claims:
        to_encode.update(claims)

    private_key, _ = get_rsa_keys()
    encoded_jwt: str = jwt.encode(to_encode, private_key, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Dict[str, Any]:
    _, public_key = get_rsa_keys()
    try:
        payload: Dict[str, Any] = jwt.decode(token, public_key, algorithms=[settings.JWT_ALGORITHM], issuer="qa-agentos")
        return payload
    except jwt.PyJWTError as e:
        raise UnauthorizedError(message=f"Invalid or expired JWT token: {str(e)}") from e
