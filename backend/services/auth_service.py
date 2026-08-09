import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.config import get_config
from backend.models.tables import AppSession, AppUser


def hash_password(password: str, *, iterations: int = 120000) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return (
        f"pbkdf2_sha256${iterations}$"
        f"{base64.b64encode(salt).decode()}$"
        f"{base64.b64encode(dk).decode()}"
    )


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    if not stored.startswith("pbkdf2_sha256$"):
        # Backward compatible plain-text entry from config for quick setup.
        return hmac.compare_digest(password, stored)
    try:
        _, iter_str, salt_b64, hash_b64 = stored.split("$", 3)
        iterations = int(iter_str)
        salt = base64.b64decode(salt_b64.encode())
        expected = base64.b64decode(hash_b64.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _auth_users_from_config() -> list[dict]:
    cfg = get_config().get("auth") or {}
    users = cfg.get("users") or []
    if users:
        return users
    # Safe defaults for first-run. Must be changed by admin.
    return [
        {"username": "admin", "password": "admin123", "role": "admin"},
        {"username": "readonly", "password": "readonly123", "role": "readonly"},
    ]


def ensure_seed_users(db: Session) -> None:
    users = _auth_users_from_config()
    for row in users:
        username = str(row.get("username") or "").strip()
        if not username:
            continue
        role = str(row.get("role") or "readonly").lower()
        role = "admin" if role == "admin" else "readonly"
        password_hash = row.get("password_hash")
        plain = row.get("password")
        if not password_hash and plain:
            password_hash = hash_password(str(plain))
        if not password_hash:
            continue
        user = db.query(AppUser).filter(AppUser.username == username).first()
        if not user:
            user = AppUser(
                username=username,
                password_hash=password_hash,
                role=role,
                is_active=True,
            )
            db.add(user)
        else:
            user.role = role
            user.is_active = True
            # Update hash only if explicitly provided in config.
            if row.get("password_hash") or row.get("password"):
                user.password_hash = password_hash
    db.commit()


def create_session(db: Session, user_id: int) -> AppSession:
    ttl_hours = int((get_config().get("auth") or {}).get("session_ttl_hours", 24))
    expires = datetime.utcnow() + timedelta(hours=max(1, ttl_hours))
    token = secrets.token_urlsafe(48)
    session = AppSession(token=token, user_id=user_id, expires_at=expires)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def delete_session(db: Session, token: str | None) -> None:
    if not token:
        return
    db.query(AppSession).filter(AppSession.token == token).delete()
    db.commit()


def get_user_by_session(db: Session, token: str | None) -> AppUser | None:
    if not token:
        return None
    sess = (
        db.query(AppSession)
        .filter(AppSession.token == token, AppSession.expires_at > datetime.utcnow())
        .first()
    )
    if not sess:
        return None
    user = db.query(AppUser).filter(AppUser.id == sess.user_id, AppUser.is_active == True).first()
    return user
