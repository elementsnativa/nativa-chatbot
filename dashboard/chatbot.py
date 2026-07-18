"""Chatbot tab — knowledge editor + system prompt viewer."""

import os

from fastapi import APIRouter
from pydantic import BaseModel

from dashboard._shared import _auth

router = APIRouter()
KNOWLEDGE_PATH = os.path.join(os.path.dirname(__file__), "..", "conocimiento.txt")


@router.get("/api/dashboard/knowledge")
def get_knowledge(secret: str = ""):
    _auth(secret)
    try:
        with open(KNOWLEDGE_PATH, encoding="utf-8") as f:
            return {"content": f.read()}
    except FileNotFoundError:
        return {"content": ""}


class KnowledgeBody(BaseModel):
    content: str


@router.post("/api/dashboard/knowledge")
def save_knowledge(body: KnowledgeBody, secret: str = ""):
    _auth(secret)
    with open(KNOWLEDGE_PATH, "w", encoding="utf-8") as f:
        f.write(body.content)
    return {"status": "ok"}


@router.get("/api/dashboard/prompt")
def get_prompt(secret: str = ""):
    _auth(secret)
    from prompts import SYSTEM_PROMPT
    sections = []
    current_title = "General"
    current_lines: list[str] = []
    for line in SYSTEM_PROMPT.split("\n"):
        if line.startswith("━━"):
            if current_lines:
                sections.append({"title": current_title, "content": "\n".join(current_lines).strip()})
            current_title = line.replace("━━", "").strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        sections.append({"title": current_title, "content": "\n".join(current_lines).strip()})
    return {"sections": sections}
