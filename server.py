"""
Minimal web server for the Battle Cats route planner.

Run locally:
    uvicorn server:app --reload
then open http://127.0.0.1:8000/  (interactive docs at /docs)

On Render, the Start Command is:
    uvicorn server:app --host 0.0.0.0 --port $PORT
and REDIS_URL should be set to the Key Value instance's internal URL (see rate_limit.py).
Don't add --proxy-headers / --forwarded-allow-ips: rate_limit.ClientIPMiddleware handles
X-Forwarded-For instead (uvicorn's "*" trusts the client-controlled leftmost entry).
"""

import csv
import os
from typing import Literal

import requests
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from data_download import (
    RateLimitError,
    build_meta,
    cells_to_csv,
    download,
    fetched_at_of,
    parse_tracks,
    validate_url,
)
from fetch_gacha_units import ICONS_DIR
from gacha_units import (
    group_by_rarity,
    list_all_collab_unit_names,
    list_gacha_events,
    load_gacha_units,
    suggest_event,
)
from rate_limit import ClientIPMiddleware, godfat_fetch_gate, optimize_slot, rate_limited
from route_optimizer import parse_pool, solve
from unit_rarity import load_unit_rarities

MAX_MEOWS = 100

# /optimize is CPU-heavy (a full DFS search), so its inputs are capped defensively, on
# top of the per-IP and concurrency limits in rate_limit.py.
MAX_OPTIMIZE_CSV_CHARS = 2_000_000  # a full 120-roll event's tracks.csv is ~18 KB
MAX_TARGET_UNITS = 25
MAX_ROLL_LIMIT = 200  # matches the frontend's roll-limit input (docs/index.html)

app = FastAPI(title="BC Route Planner")

# CORS: which web pages may call this API from a browser. Origins have no path or trailing slash.
# Any localhost port is allowed so the frontend can be tested locally before deploying.
# allow_headers includes Content-Type because /optimize takes a JSON body, which browsers
# preflight (it isn't a CORS "simple" request like the GET routes below).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://infinitepower85.github.io"],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    expose_headers=["Retry-After"],
)
# Added last so it runs first: everything after it (incl. rate limiting) sees the real client IP.
app.add_middleware(ClientIPMiddleware)

# /whoami echoes the client IP the server sees. Off by default; turn on briefly after a
# deploy (ENABLE_WHOAMI=1) to check ClientIPMiddleware against Render's real proxy -- a
# forged X-Forwarded-For must not change the "ip" it reports. See rate_limit.py.
ENABLE_WHOAMI = os.environ.get("ENABLE_WHOAMI") == "1"


class CachedStaticFiles(StaticFiles):
    """StaticFiles with a long browser cache lifetime. Unit icons (see
    fetch_gacha_units.py) don't change once downloaded, so there's no reason for a
    repeat visit to re-fetch or even revalidate them."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=604800, immutable"  # 1 week
        return response


ICONS_DIR.mkdir(parents=True, exist_ok=True)  # StaticFiles requires the directory to exist
app.mount("/icons", CachedStaticFiles(directory=str(ICONS_DIR)), name="icons")


@app.get("/")
def hello_world() -> str:
    return "Hello World"


if ENABLE_WHOAMI:
    @app.get("/whoami")
    def whoami(request: Request) -> dict:
        return {"ip": request.client.host if request.client else None,
                "x_forwarded_for": request.headers.get("x-forwarded-for")}


@app.get("/meow", dependencies=[rate_limited("low")])
def meow(n: int = Query(ge=1, le=MAX_MEOWS)) -> str:
    return " ".join(["meow"] * n)


@app.get("/tracks", dependencies=[rate_limited("tracks")])
def get_tracks(url: str) -> dict:
    """Fetch and parse a bc.godfat.org tracks link. Reuses data_download.py's parsing and
    cache; its shared hourly cap is counted via rate_limit.godfat_fetch_gate, so it
    survives restarts."""
    try:
        query = validate_url(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        html = download(url, record_fetch=godfat_fetch_gate)
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


@app.get("/gacha-events", dependencies=[rate_limited("low")])
def get_gacha_events() -> list[str]:
    """Event folder names under data/gacha_pools that have a units CSV (see
    fetch_gacha_units.py), for the frontend's event dropdown."""
    return list_gacha_events()


@app.get("/gacha-units", dependencies=[rate_limited("low")])
def get_gacha_units(event: str) -> dict:
    """That event's unit roster, grouped by rarity (sorted alphabetically within each
    group), for the frontend's target-unit checkboxes."""
    try:
        units = load_gacha_units(event)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"event": event, "rarities": group_by_rarity(units)}


@app.get("/unit-rarities", dependencies=[rate_limited("low")])
def get_unit_rarities() -> dict:
    """name -> rarity for every unit's Normal form (see unit_rarity.py), for the
    frontend roll simulator's "Collected" panel. {} if data/unit_data isn't present."""
    return load_unit_rarities()


@app.get("/collab-units", dependencies=[rate_limited("low")])
def get_collab_units() -> list[str]:
    """Every unit name across all fetched events' collab rosters (see
    gacha_units.list_all_collab_unit_names()), for the frontend roll simulator's
    "collab units only" filter on its Collected panel."""
    return list_all_collab_unit_names()


@app.get("/match-event", dependencies=[rate_limited("low")])
def get_match_event(text: str) -> dict:
    """Best-effort guess at which known event a Godfat banner's display text (meta.banner
    from /tracks) is for -- see gacha_units.suggest_event(). A convenience pre-selection
    for the frontend's event dropdown only; {"event": null} means no confident guess."""
    return {"event": suggest_event(text)}


class OptimizeRequest(BaseModel):
    csv: str = Field(max_length=MAX_OPTIMIZE_CSV_CHARS)
    target_units: list[str] = Field(min_length=1, max_length=MAX_TARGET_UNITS)
    max_rolls: int = Field(ge=1, le=MAX_ROLL_LIMIT)
    start_track: Literal["A", "B"] = "A"
    start_roll: int = Field(ge=1, default=1)
    max_elevens: int | None = Field(ge=0, default=None)  # None: unlimited guaranteed-11s


@app.post("/optimize", dependencies=[rate_limited("optimize"), Depends(optimize_slot)])
def optimize(req: OptimizeRequest) -> dict:
    """Run the route optimizer against a tracks CSV the client sends us -- nothing is
    looked up or stored server-side. See route_optimizer.py and the CSV-vs-URL
    discussion in service_plan.md for why the client sends the data itself."""
    try:
        pool = parse_pool(req.csv)
    except (csv.Error, KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Could not parse csv: {e}")

    result = solve(pool, set(req.target_units), req.max_rolls,
                    start_track=req.start_track, start_roll=req.start_roll, max_elevens=req.max_elevens)
    return {
        "score": result.score, "collected": result.collected, "route": result.route,
        "elevens_used": result.elevens_used,
    }
