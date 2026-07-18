"""
dashboard/ — Admin panel package.

Exports a single FastAPI router that combines:
  - chatbot.py   (Chatbot tab: knowledge + prompt)
  - flows.py     (Automatizaciones tab: ig_flows CRUD)
  - carritos.py  (Carritos tab: stats + template config)

Mount via:
  from dashboard import router as dashboard_router
  app.include_router(dashboard_router)
"""

import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from dashboard._shared import ADMIN_SECRET
from dashboard import chatbot, flows, carritos

router = APIRouter()

_HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "dashboard.html")


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(secret: str = ""):
    if secret != ADMIN_SECRET:
        return HTMLResponse("<h3>Acceso denegado</h3>", status_code=403)
    with open(_HTML_PATH, encoding="utf-8") as f:
        return HTMLResponse(f.read())


router.include_router(chatbot.router)
router.include_router(flows.router)
router.include_router(carritos.router)
