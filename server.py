"""
Minimal web server for the Battle Cats route planner.

Run locally:
    uvicorn server:app --reload
then open http://127.0.0.1:8000/  (interactive docs at /docs)

On Render, the Start Command is:
    uvicorn server:app --host 0.0.0.0 --port $PORT
"""

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from data_download import (
    RateLimitError,
    build_meta,
    cells_to_csv,
    download,
    fetched_at_of,
    parse_tracks,
    validate_url,
)

MAX_MEOWS = 100

app = FastAPI(title="BC Route Planner")

# CORS: which web pages may call this API from a browser. Origins have no path or trailing slash.
# Any localhost port is allowed so the frontend can be tested locally before deploying.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://infinitepower85.github.io"],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["GET"],
)


@app.get("/")
def hello_world() -> str:
    return "Hello World"


@app.get("/meow")
def meow(n: int = Query(ge=1, le=MAX_MEOWS)) -> str:
    return " ".join(["meow"] * n)


@app.get("/tracks")
def get_tracks(url: str) -> dict:
    """Fetch and parse a bc.godfat.org tracks link. Reuses data_download.py as-is,
    including its own cache and hourly-request cap (see that file for details)."""
    try:
        query = validate_url(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        html = download(url)
    except RateLimitError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Could not reach bc.godfat.org: {e}")

    cells, event_name = parse_tracks(html)
    if not cells:
        raise HTTPException(status_code=502, detail="Parsed 0 cells; the page layout may have changed.")

    event_id = query["event"][0]
    meta = build_meta(query["seed"][0], event_id, event_name, url, fetched_at_of(url), len(cells))
    return {"meta": meta, "csv": cells_to_csv(cells)}
