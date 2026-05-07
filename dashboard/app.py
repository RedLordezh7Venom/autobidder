"""
dashboard/app.py — FastAPI dashboard backend.
Serves the monitoring UI and proxies actions to the worker API.
"""
import os
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://bidbot:password@localhost:5432/bidding_bot")
WORKER_API_URL = os.getenv("WORKER_API_URL", "http://worker:8080")
WORKER_SECRET = os.getenv("WORKER_SECRET", "changeme")

_db_url = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
engine = create_async_engine(_db_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

templates = Jinja2Templates(directory="templates")

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(title="AutoBid Dashboard", lifespan=lifespan)

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


async def worker_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{WORKER_API_URL}{path}",
            headers={"Authorization": f"Bearer {WORKER_SECRET}"},
        )
        return r.json()


async def worker_post(path: str, data: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{WORKER_API_URL}{path}",
            json=data,
            headers={"Authorization": f"Bearer {WORKER_SECRET}"},
        )
        return r.json()


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    try:
        stats = await worker_get("/stats")
    except Exception:
        stats = {}

    async with SessionLocal() as session:
        # Recent 20 interactions
        r = await session.execute(text(
            "SELECT platform, author_name, status, created_at, post_url, "
            "comment_posted, error_message "
            "FROM interactions ORDER BY created_at DESC LIMIT 20"
        ))
        rows = r.fetchall()
        interactions = [
            {
                "platform": row[0], "author": row[1], "status": row[2],
                "created_at": row[3].strftime("%Y-%m-%d %H:%M"),
                "url": row[4], "comment": row[5], "error": row[6],
            }
            for row in rows
        ]

        # Keywords
        kr = await session.execute(text(
            "SELECT id, platform, keyword, enabled FROM keywords ORDER BY platform, keyword"
        ))
        keywords = [
            {"id": str(row[0]), "platform": row[1], "keyword": row[2], "enabled": row[3]}
            for row in kr.fetchall()
        ]

        # Skills
        sr = await session.execute(text(
            "SELECT id, category, skill, proficiency FROM skills_profile ORDER BY category, skill"
        ))
        skills = [
            {"id": str(row[0]), "category": row[1], "skill": row[2], "proficiency": row[3]}
            for row in sr.fetchall()
        ]

        # Config
        cr = await session.execute(text("SELECT key, value, description FROM system_config ORDER BY key"))
        config = [{"key": row[0], "value": row[1], "description": row[2]} for row in cr.fetchall()]

    return templates.TemplateResponse("index.html", {
        "request": request,
        "stats": stats,
        "interactions": interactions,
        "keywords": keywords,
        "skills": skills,
        "config": config,
        "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.post("/reset-circuit")
async def reset_circuit(platform: str = Form(...)):
    await worker_post("/reset", {"platform": platform})
    return RedirectResponse("/", status_code=303)


@app.post("/config/update")
async def update_config(key: str = Form(...), value: str = Form(...)):
    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE system_config SET value=:v, updated_at=NOW() WHERE key=:k"),
            {"v": value, "k": key},
        )
        await session.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/keyword/toggle")
async def toggle_keyword(keyword_id: str = Form(...)):
    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE keywords SET enabled = NOT enabled WHERE id=:id::uuid"),
            {"id": keyword_id},
        )
        await session.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/keyword/add")
async def add_keyword(platform: str = Form(...), keyword: str = Form(...)):
    async with SessionLocal() as session:
        await session.execute(
            text("INSERT INTO keywords (platform, keyword) VALUES (:p, :k) ON CONFLICT DO NOTHING"),
            {"p": platform, "k": keyword},
        )
        await session.commit()
    return RedirectResponse("/", status_code=303)


@app.get("/api/stats")
async def api_stats():
    """Live stats endpoint for dashboard auto-refresh."""
    try:
        return await worker_get("/stats")
    except Exception as e:
        return {"error": str(e)}
