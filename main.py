"""
main.py
-------
FastAPI application. Thin orchestration layer.
"""

import os
import sys

# Windows: switch to ProactorEventLoop so Playwright subprocesses work
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from ai_engine import generate_insights
from scraper import scrape_page

load_dotenv()

app = FastAPI(
    title="Page Audit Tool",
    version="3.0.0",
    description="Universal website auditor — works on all tech stacks.",
)


class AuditRequest(BaseModel):
    url: str


@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.post("/audit", response_class=JSONResponse)
async def audit(request: AuditRequest):
    url = request.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    if not os.environ.get("OPENROUTER_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="OPENROUTER_API_KEY not set. Add it to your .env file.",
        )

    # Step 1: Scrape
    try:
        metrics = scrape_page(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scraping failed: {str(e)}")

    # Step 2: AI insights
    try:
        insights = generate_insights(metrics)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI analysis failed: {str(e)}")

    # Strip internal field before returning
    metrics_clean = {k: v for k, v in metrics.items() if k != "page_content_sample"}

    return {"metrics": metrics_clean, "insights": insights}


@app.get("/health")
async def health():
    return {"status": "ok", "version": "3.0.0"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
