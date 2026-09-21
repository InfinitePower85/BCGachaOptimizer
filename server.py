"""
Minimal web server for the Battle Cats route planner.

Run locally:
    uvicorn server:app --reload
then open http://127.0.0.1:8000/  (interactive docs at /docs)

On Render, the Start Command is:
    uvicorn server:app --host 0.0.0.0 --port $PORT
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

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
