"""Authentification JWT et contrôle d'accès par rôle (admin / user) de l'API."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import jwt

ALGORITHM = "HS256"
TOKEN_MINUTES = 60
ROLES = ("admin", "user")


@dataclass(frozen=True)
class User:
    username: str
    role: str
    salt: bytes
    password_hash: bytes


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)


@lru_cache(maxsize=1)
def users() -> dict[str, User]:
    """Comptes de démonstration ; en PRD l'authentification est déléguée à l'AD (Kerberos/OIDC)."""
    accounts = {
        "admin": ("admin", os.environ.get("UNIFY_ADMIN_PASSWORD", "Admin#2026!")),
        "user": ("user", os.environ.get("UNIFY_USER_PASSWORD", "User#2026!")),
    }
    result = {}
    for username, (role, password) in accounts.items():
        salt = secrets.token_bytes(16)
        result[username] = User(username, role, salt, hash_password(password, salt))
    return result


@lru_cache(maxsize=1)
def jwt_secret() -> str:
    return os.environ.get("UNIFY_JWT_SECRET") or secrets.token_hex(32)


def authenticate(username: str, password: str) -> User | None:
    user = users().get(username)
    if user is None:
        return None
    candidate = hash_password(password, user.salt)
    return user if hmac.compare_digest(candidate, user.password_hash) else None


def create_token(user: User, minutes: int = TOKEN_MINUTES) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user.username,
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, jwt_secret(), algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Décode et vérifie le jeton (signature + expiration). Lève jwt.PyJWTError sinon."""
    return jwt.decode(token, jwt_secret(), algorithms=[ALGORITHM])
