"""
main.py — Application entry point

Run with:    python main.py
Then open:   http://localhost:8000/         (mobile app — public view)
             http://localhost:8000/admin    (admin panel — requires login)
"""

import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from database import init_db
from api import public_router, admin_router
from auth import (
    verify_credentials, create_session_token, get_session_user,
    SESSION_COOKIE_NAME, SESSION_MAX_AGE_SECONDS,
)
from seed import seed_if_empty


# ─── LOGGING ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s  %(levelname)-7s  %(name)-15s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


# ─── LIFESPAN (startup/shutdown) ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("=" * 60)
    log.info("Starting Cognizant Competitor Pulse (admin-panel edition)")
    log.info("=" * 60)

    init_db()
    log.info("Database initialized")

    seed_if_empty()

    log.info("Ready. Mobile app:   http://localhost:%d/", settings.port)
    log.info("       Admin panel:  http://localhost:%d/admin", settings.port)
    log.info("       API docs:     http://localhost:%d/docs", settings.port)

    if settings.admin_password == "changeme":
        log.warning("⚠️  Default admin password is in use — change ADMIN_PASSWORD in .env before sharing!")

    log.info("=" * 60)
    yield


# ─── FASTAPI APP ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="Cognizant Competitor Pulse",
    description="Real-time competitive intelligence (admin-panel edition)",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],     # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routers
app.include_router(public_router)
app.include_router(admin_router)


# ─── FRONTEND FILES ──────────────────────────────────────────────────────────

FRONTEND_DIR = Path(__file__).parent / "frontend"


@app.get("/")
def serve_app():
    """Mobile app — public view."""
    return FileResponse(FRONTEND_DIR / "desktop.html")


@app.get("/admin")
def serve_admin(request: Request):
    """Admin panel — requires login."""
    if not get_session_user(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    return FileResponse(FRONTEND_DIR / "admin.html")


# ─── ADMIN LOGIN FLOW ────────────────────────────────────────────────────────

LOGIN_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Competitor Pulse — Admin Login</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:linear-gradient(135deg,#0D1243 0%,#003E80 100%);
  min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.card{background:#fff;border-radius:14px;padding:32px 30px;width:100%;max-width:380px;
  box-shadow:0 20px 60px rgba(0,0,0,0.3)}
.brand{display:flex;align-items:center;gap:10px;margin-bottom:6px}
.diamond{width:28px;height:28px;display:block}
.brand-name{font-size:18px;font-weight:900;color:#0D1243;letter-spacing:-0.3px}
.sub{font-size:11px;color:#5A6A7A;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:24px}
h1{font-size:20px;color:#0D1243;margin-bottom:6px;letter-spacing:-0.3px}
.hint{font-size:12px;color:#5A6A7A;margin-bottom:20px;line-height:1.5}
.field{margin-bottom:14px}
label{display:block;font-size:11px;font-weight:700;color:#5A6A7A;letter-spacing:0.05em;
  text-transform:uppercase;margin-bottom:5px}
input{width:100%;padding:10px 12px;border:1px solid #E2E8F2;border-radius:8px;
  font-size:14px;font-family:inherit;outline:none;transition:all .15s}
input:focus{border-color:#0057B8;box-shadow:0 0 0 3px rgba(0,87,184,0.08)}
button{width:100%;padding:11px;background:linear-gradient(135deg,#0057B8 0%,#003E80 100%);
  color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;
  margin-top:6px;transition:transform .15s}
button:hover{transform:translateY(-1px)}
.err{font-size:12px;color:#DC2626;background:#FEE2E2;padding:9px 12px;border-radius:8px;
  margin-bottom:14px;border:1px solid #FCA5A5}
.footer{font-size:11px;color:#8899AA;text-align:center;margin-top:18px}
.footer a{color:#0057B8;text-decoration:none}
</style></head><body>
<div class="card">
  <div class="brand">
    <svg class="diamond" viewBox="0 0 28 28">
      <path d="M0 14 L8 4 L20 14 L8 24 Z" fill="#0057B8"/>
      <path d="M8 4 L20 14 L8 24 Z" fill="#00B5B8" opacity="0.85"/>
      <path d="M5 14 L8 9 L11 14 L8 19 Z" fill="#fff"/>
    </svg>
    <span class="brand-name">Cognizant</span>
  </div>
  <div class="sub">Competitor Pulse · Admin</div>
  <h1>Sign in</h1>
  <div class="hint">Restricted to authorized analysts. Use credentials provided by your administrator.</div>
  __ERROR__
  <form method="post" action="/admin/login">
    <div class="field"><label>Username</label><input type="text" name="username" required autofocus></div>
    <div class="field"><label>Password</label><input type="password" name="password" required></div>
    <button type="submit">Sign in</button>
  </form>
  <div class="footer">Need access? Contact the project owner.</div>
</div>
</body></html>"""


@app.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None):
    """Render the login form."""
    # If already logged in, redirect to admin panel
    if get_session_user(request):
        return RedirectResponse(url="/admin", status_code=303)
    err_html = (
        f'<div class="err">{error}</div>' if error else ""
    )
    return HTMLResponse(LOGIN_PAGE.replace("__ERROR__", err_html))


@app.post("/admin/login")
def login_submit(
    username: str = Form(...),
    password: str = Form(...),
):
    """Handle login form submission."""
    if not verify_credentials(username, password):
        log.warning(f"Failed login attempt for username={username!r}")
        return RedirectResponse(
            url="/admin/login?error=Invalid+username+or+password",
            status_code=303,
        )

    token = create_session_token(username)
    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,   # set to True behind HTTPS in production
    )
    log.info(f"Admin {username} logged in")
    return response


@app.post("/admin/logout")
def logout():
    """Clear session and redirect to login."""
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


# ─── STATIC ASSETS ───────────────────────────────────────────────────────────

_assets = FRONTEND_DIR / "assets"
if _assets.exists():
    app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")


# ─── RUN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    import uvicorn
    # Render provides PORT env var; fall back to settings.port locally
    port = int(os.environ.get("PORT", settings.port))
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=port,
        reload=False,
    )
