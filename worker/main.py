"""
main.py — Single-process FastAPI app. No Docker, no Postgres, no Redis, no n8n.

Run with:
    cd worker
    uvicorn main:app --reload --port 8080

Or from repo root:
    python worker/main.py
"""
import asyncio
import os
import random
import sys
from contextlib import asynccontextmanager
from datetime import datetime, time
from pathlib import Path
from typing import Literal, Optional

import structlog
from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text

import database as db
from ai_client import classify_intent, extract_pain_point, generate_bid
from alerting import alert_bid_posted, alert_circuit_tripped
from config import get_settings

settings = get_settings()

# ── Logging ───────────────────────────────────────────────────────────────────
import logging
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if settings.log_level == "DEBUG"
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, settings.log_level.upper(), logging.INFO)
    ),
    logger_factory=structlog.PrintLoggerFactory(),
)
logger = structlog.get_logger(__name__)

# ── Ensure local dirs exist ───────────────────────────────────────────────────
Path(settings.sessions_dir).mkdir(parents=True, exist_ok=True)
Path(settings.screenshots_dir).mkdir(parents=True, exist_ok=True)


# ── App lifecycle ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("worker.starting")
    await db.init_db()          # Creates SQLite tables + seeds defaults
    logger.info("worker.ready")
    yield
    logger.info("worker.shutting_down")


app = FastAPI(
    title="AutoBid Worker API",
    version="2.0.0",
    description="Playwright-powered LinkedIn & X automation worker — SQLite edition",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the demo frontend from /static and redirect / to it
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

@app.get("/", include_in_schema=False)
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/index.html")

# ── Auth ──────────────────────────────────────────────────────────────────────
security = HTTPBearer(auto_error=False)


def require_auth(creds: Optional[HTTPAuthorizationCredentials] = Security(security)):
    secret = settings.worker_secret
    if secret == "changeme":
        return creds  # dev mode — skip auth
    if not creds or creds.credentials != secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return creds


# ── Request / Response Models ─────────────────────────────────────────────────
class ScrapeRequest(BaseModel):
    platform: Literal["upwork", "reddit", "both"]
    keywords: Optional[list[str]] = None
    max_posts_per_keyword: int = 15


class PostRequest(BaseModel):
    platform: Literal["upwork", "reddit"]
    post_id: str
    post_url: str
    post_content: str
    author_name: str


class ResetRequest(BaseModel):
    platform: Literal["upwork", "reddit"]


class BidDraftRequest(BaseModel):
    """Lightweight endpoint — just generate a bid text, no Playwright needed."""
    post_content: str
    platform: Literal["upwork", "reddit"] = "upwork"


# ── Business hours helper ─────────────────────────────────────────────────────
async def _in_business_hours() -> bool:
    start_str = await db.get_config("business_hours_start", "09:00")
    end_str   = await db.get_config("business_hours_end",   "18:00")
    now_time  = datetime.now().time()
    start = time(*map(int, start_str.split(":")))
    end   = time(*map(int, end_str.split(":")))
    return start <= now_time <= end


# ── Rate limit helper ─────────────────────────────────────────────────────────
async def _check_rate_limit(platform: str, account_id: str) -> tuple[bool, int]:
    limit_key = f"{platform}_daily_limit"
    default   = "12" if platform == "linkedin" else "7"
    limit     = int(await db.get_config(limit_key, default))
    count     = await db.get_daily_count(account_id, platform)
    return count < limit, count


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat(), "db": "sqlite"}


@app.get("/stats")
async def stats():
    """Live statistics — consumed by the dashboard."""
    async with db.get_db() as session:
        r = await session.execute(text("SELECT status, COUNT(*) FROM interactions GROUP BY status"))
        status_counts = {row[0]: row[1] for row in r.fetchall()}

        r = await session.execute(text(
            "SELECT platform, COUNT(*) FROM interactions "
            "WHERE DATE(created_at) = DATE('now') GROUP BY platform"
        ))
        today_counts = {row[0]: row[1] for row in r.fetchall()}

        r = await session.execute(text(
            "SELECT platform, tripped, failure_count FROM circuit_breaker_state"
        ))
        circuits = {row[0]: {"tripped": bool(row[1]), "failures": row[2]} for row in r.fetchall()}

        r = await session.execute(text(
            "SELECT platform, author_name, status, created_at, post_url "
            "FROM interactions ORDER BY created_at DESC LIMIT 10"
        ))
        recent = [
            {"platform": row[0], "author": row[1], "status": row[2],
             "created_at": row[3], "url": row[4]}
            for row in r.fetchall()
        ]

    return {
        "status_counts": status_counts,
        "today_counts": today_counts,
        "circuit_breakers": circuits,
        "recent_interactions": recent,
        "business_hours": await _in_business_hours(),
    }


@app.post("/draft")
async def draft_bid(req: BidDraftRequest):
    """
    Demo-friendly endpoint: classify + extract pain point + generate bid.
    No Playwright — pure AI pipeline. Great for testing without real accounts.
    """
    is_real = await classify_intent(req.post_content)
    if not is_real:
        return {"is_opportunity": False, "reason": "Not a genuine opportunity per classifier"}

    pain_point = await extract_pain_point(req.post_content)
    bid_text, ai_prompt, model_used = await generate_bid(req.post_content, pain_point, req.platform)

    return {
        "is_opportunity": True,
        "pain_point": pain_point,
        "bid_text": bid_text,
        "model_used": model_used,
    }


@app.post("/scrape")
async def scrape(req: ScrapeRequest):
    """Scrape Reddit and/or Upwork via free public APIs. No credentials needed."""
    if not await _in_business_hours():
        return {"posts": [], "skipped_reason": "outside_business_hours"}

    platforms = ["upwork", "reddit"] if req.platform == "both" else [req.platform]
    all_posts = []
    
    import httpx
    import re

    async with httpx.AsyncClient(timeout=10) as client:
        for platform in platforms:
            if await db.is_circuit_tripped(platform):
                continue

            keywords = req.keywords or await db.get_keywords(platform)

            for keyword in keywords[:3]:
                try:
                    raw_posts = []
                    if platform == "reddit":
                        # Reddit r/forhire public JSON API
                        headers = {"User-Agent": "AutoBidBot/1.0"}
                        url = "https://www.reddit.com/r/forhire/new.json?limit=10"
                        r = await client.get(url, headers=headers)
                        if r.status_code == 200:
                            data = r.json()
                            for child in data.get("data", {}).get("children", []):
                                post = child["data"]
                                if "[hiring]" in post.get("title", "").lower() and keyword.lower() in post.get("selftext", "").lower() + post.get("title", "").lower():
                                    raw_posts.append({
                                        "post_id": f"reddit_{post['id']}",
                                        "url": f"https://reddit.com{post['permalink']}",
                                        "content": f"{post['title']}\n{post['selftext']}",
                                        "author": post["author"]
                                    })
                    else:
                        # Upwork RSS feed
                        url = f"https://www.upwork.com/ab/feed/jobs/rss?q={keyword}"
                        headers = {"User-Agent": "Mozilla/5.0"}
                        r = await client.get(url, headers=headers)
                        if r.status_code == 200:
                            xml = r.text
                            items = xml.split("<item>")
                            for item in items[1:]:
                                title = re.search(r"<title>(.*?)</title>", item)
                                link = re.search(r"<link>(.*?)</link>", item)
                                desc = re.search(r"<description>(.*?)</description>", item)
                                if title and link and desc:
                                    # Clean up HTML entities roughly
                                    desc_clean = desc.group(1).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
                                    desc_clean = re.sub(r"<[^>]+>", " ", desc_clean)
                                    post_id = link.group(1).split("_~")[-1].split("?")[0] if "_~" in link.group(1) else title.group(1)[:15]
                                    raw_posts.append({
                                        "post_id": f"upwork_{post_id}",
                                        "url": link.group(1),
                                        "content": f"{title.group(1)}\n{desc_clean}",
                                        "author": "Upwork Client"
                                    })

                    # Filter duplicates
                    new_posts = [
                        p for p in raw_posts
                        if not await db.is_duplicate(platform, p["post_id"])
                    ]
                    all_posts.extend(new_posts[:req.max_posts_per_keyword])
                    await db.reset_circuit(platform)

                except Exception as e:
                    logger.error("scrape.failed", keyword=keyword, platform=platform, error=str(e))
                    threshold = int(await db.get_config("circuit_breaker_threshold", "3"))
                    await db.record_failure(platform, threshold)

    return {"posts": all_posts, "count": len(all_posts)}


@app.post("/post")
async def post_bid(req: PostRequest):
    """Full pipeline: classify → pain point → bid → simulate post via API → record."""
    platform = req.platform

    if not await _in_business_hours():
        return {"success": False, "reason": "outside_business_hours"}
    if await db.is_circuit_tripped(platform):
        return {"success": False, "reason": "circuit_tripped"}
    if await db.is_duplicate(platform, req.post_id):
        return {"success": False, "reason": "already_posted"}

    account_id = f"free_bot_{platform}"
    ok, count = await _check_rate_limit(platform, account_id)
    if not ok:
        return {"success": False, "reason": "daily_limit_reached", "count": count}

    is_real = await classify_intent(req.post_content)
    if not is_real:
        await db.record_interaction(
            platform=platform, post_id=req.post_id, post_url=req.post_url,
            post_content=req.post_content, author_name=req.author_name,
            account_id=account_id, status="skipped",
            error_message="Not a genuine opportunity",
        )
        return {"success": False, "reason": "not_a_real_opportunity"}

    pain_point = await extract_pain_point(req.post_content)
    bid_text, ai_prompt, model_used = await generate_bid(req.post_content, pain_point, platform)

    # Free tools simulation: we don't actually post to Upwork/Reddit without an API key, 
    # we just simulate success for the demo.
    success = True
    await asyncio.sleep(1) # Simulate network delay
    logger.info("post.simulated_success", platform=platform, url=req.post_url)

    final_status = "posted"
    await db.record_interaction(
        platform=platform, post_id=req.post_id, post_url=req.post_url,
        post_content=req.post_content, author_name=req.author_name,
        account_id=account_id, status=final_status,
        ai_model=model_used, ai_prompt=ai_prompt,
        ai_response=bid_text, comment_posted=bid_text,
    )

    await db.increment_daily_count(account_id, platform)
    await db.reset_circuit(platform)

    return {"success": success, "bid_text": bid_text, "model_used": model_used, "pain_point": pain_point}


@app.post("/reset")
async def reset_circuit(req: ResetRequest):
    """Manually reset a tripped circuit breaker."""
    await db.reset_circuit(req.platform)
    logger.info("circuit.reset_manually", platform=req.platform)
    return {"success": True, "platform": req.platform}


# ── Embedded dashboard (no separate container needed) ─────────────────────────
@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoBid Dashboard</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  :root{
    --bg:#0f1117;--surface:#1a1d27;--border:#2a2d3e;
    --primary:#6c63ff;--green:#22c55e;--red:#ef4444;--yellow:#f59e0b;
    --text:#e2e8f0;--muted:#64748b;
  }
  body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding:2rem}
  h1{font-size:1.6rem;font-weight:700;margin-bottom:1.5rem;display:flex;align-items:center;gap:.75rem}
  h1 span.dot{width:10px;height:10px;border-radius:50%;background:var(--green);box-shadow:0 0 8px var(--green);display:inline-block}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1rem;margin-bottom:1.5rem}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:1.25rem}
  .card h3{font-size:.75rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:.5rem}
  .card .val{font-size:2rem;font-weight:700}
  .card .val.green{color:var(--green)} .card .val.red{color:var(--red)} .card .val.yellow{color:var(--yellow)}
  .section{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:1.25rem;margin-bottom:1rem}
  .section h2{font-size:.9rem;font-weight:600;margin-bottom:1rem;color:var(--muted)}
  table{width:100%;border-collapse:collapse;font-size:.85rem}
  th{text-align:left;padding:.5rem .75rem;color:var(--muted);font-weight:500;border-bottom:1px solid var(--border)}
  td{padding:.6rem .75rem;border-bottom:1px solid var(--border)}
  tr:last-child td{border-bottom:none}
  .badge{display:inline-block;padding:.15rem .5rem;border-radius:999px;font-size:.75rem;font-weight:600}
  .badge.posted{background:#14532d;color:#4ade80}
  .badge.skipped{background:#1c1917;color:#a8a29e}
  .badge.failed{background:#450a0a;color:#f87171}
  .circuit{display:flex;gap:1rem;flex-wrap:wrap}
  .chip{display:flex;align-items:center;gap:.4rem;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:.5rem .9rem;font-size:.85rem}
  .chip .led{width:8px;height:8px;border-radius:50%}
  .led.ok{background:var(--green);box-shadow:0 0 6px var(--green)}
  .led.tripped{background:var(--red);box-shadow:0 0 6px var(--red)}
  .refresh{margin-left:auto;font-size:.8rem;color:var(--muted);cursor:pointer;background:none;border:1px solid var(--border);border-radius:6px;padding:.3rem .7rem;color:var(--text)}
  .refresh:hover{background:var(--border)}
</style>
</head>
<body>
<h1><span class="dot"></span> AutoBid Live Dashboard <button class="refresh" onclick="load()">↻ Refresh</button></h1>

<div class="grid" id="stat-cards">
  <div class="card"><h3>Loading...</h3><div class="val">—</div></div>
</div>

<div class="section">
  <h2>Circuit Breakers</h2>
  <div class="circuit" id="circuits">—</div>
</div>

<div class="section">
  <h2>Recent Activity</h2>
  <table>
    <thead><tr><th>Platform</th><th>Author</th><th>Status</th><th>Time</th><th>URL</th></tr></thead>
    <tbody id="recent-tbody"><tr><td colspan="5">Loading…</td></tr></tbody>
  </table>
</div>

<script>
async function load(){
  try{
    const r=await fetch('/stats',{headers:{'Authorization':'Bearer changeme'}});
    const d=await r.json();

    // Stat cards
    const total=Object.values(d.status_counts||{}).reduce((a,b)=>a+b,0);
    const posted=d.status_counts?.posted||0;
    const failed=d.status_counts?.failed||0;
    const skipped=d.status_counts?.skipped||0;
    const todayLI=d.today_counts?.linkedin||0;
    const todayX=d.today_counts?.x||0;
    document.getElementById('stat-cards').innerHTML=`
      <div class="card"><h3>Total Interactions</h3><div class="val">${total}</div></div>
      <div class="card"><h3>Posted</h3><div class="val green">${posted}</div></div>
      <div class="card"><h3>Skipped</h3><div class="val yellow">${skipped}</div></div>
      <div class="card"><h3>Failed</h3><div class="val red">${failed}</div></div>
      <div class="card"><h3>Today — LinkedIn</h3><div class="val">${todayLI}</div></div>
      <div class="card"><h3>Today — X</h3><div class="val">${todayX}</div></div>
      <div class="card"><h3>Business Hours</h3><div class="val ${d.business_hours?'green':'yellow'}">${d.business_hours?'Active':'Off'}</div></div>
    `;

    // Circuits
    const cb=d.circuit_breakers||{};
    document.getElementById('circuits').innerHTML=Object.entries(cb).map(([p,s])=>
      `<div class="chip"><div class="led ${s.tripped?'tripped':'ok'}"></div>
       <strong>${p}</strong> — ${s.tripped?'TRIPPED':'OK'} (${s.failures} failures)</div>`
    ).join('');

    // Recent
    const rows=(d.recent_interactions||[]).map(i=>`
      <tr>
        <td>${i.platform}</td>
        <td>${i.author||'—'}</td>
        <td><span class="badge ${i.status}">${i.status}</span></td>
        <td>${i.created_at?.slice(0,19)||'—'}</td>
        <td>${i.url?`<a href="${i.url}" target="_blank" style="color:#6c63ff">↗</a>`:'—'}</td>
      </tr>`).join('');
    document.getElementById('recent-tbody').innerHTML=rows||'<tr><td colspan="5">No interactions yet</td></tr>';
  }catch(e){console.error(e);}
}
load();
setInterval(load,15000);
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
