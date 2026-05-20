from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from fastapi import Request
from fastapi.responses import RedirectResponse

from app.config import get_settings

COOKIE_NAME = "gowild_session"
SESSION_MAX_AGE = 86400  # 24 hours


def _get_serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(settings.SECRET_KEY)


def create_session_cookie(response, password: str) -> bool:
    settings = get_settings()
    if password != settings.ADMIN_PASSWORD:
        return False
    token = _get_serializer().dumps({"auth": True})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return True


def verify_session(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        _get_serializer().loads(token, max_age=SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


class AuthRedirect(Exception):
    pass


async def require_auth(request: Request) -> None:
    if not verify_session(request):
        raise AuthRedirect()
