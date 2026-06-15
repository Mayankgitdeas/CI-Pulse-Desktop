"""
auth.py — Admin authentication

Simple session-based login. The admin enters username/password on /admin/login,
gets a signed session cookie, and can then access /admin/* pages and
/api/admin/* endpoints.

This is intentionally simple for the pilot. For production:
  - Replace single ADMIN_USERNAME with a users table
  - Move passwords to bcrypt-hashed storage
  - Add Azure AD / Entra ID SSO instead of password login
  - Add rate limiting to prevent brute force
"""

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse

from config import settings


# Signer for session cookies. Uses HMAC-SHA256 with the SESSION_SECRET.
_serializer = URLSafeTimedSerializer(settings.session_secret, salt="pulse-admin")

SESSION_COOKIE_NAME = "pulse_admin_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 8  # 8 hours


def verify_credentials(username: str, password: str) -> bool:
    """Check if username/password match the configured admin credentials.

    For the pilot we use a single hard-coded admin (from .env). In production,
    swap for a query against a users table with bcrypt-hashed passwords."""
    return (
        username == settings.admin_username
        and password == settings.admin_password
    )


def create_session_token(username: str) -> str:
    """Create a signed session token for the given user."""
    return _serializer.dumps({"username": username})


def get_session_user(request: Request) -> str | None:
    """Read the session cookie and return the logged-in user, or None."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
        return data.get("username")
    except (BadSignature, SignatureExpired):
        return None


def require_admin(request: Request) -> str:
    """FastAPI dependency that ensures the user is logged in.
    Raises HTTPException 401 if not — gets caught by exception handler
    to redirect to login page."""
    user = get_session_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not logged in",
        )
    return user


def redirect_to_login() -> RedirectResponse:
    """Convenience: redirect unauthenticated users to the login page."""
    return RedirectResponse(url="/admin/login", status_code=303)
