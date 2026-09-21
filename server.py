"""
Minimal web server for the Battle Cats route planner.

Run locally:
    uvicorn server:app --reload
then open http://127.0.0.1:8000/  (interactive docs at /docs)

On Render, the Start Command is:
    uvicorn server:app --host 0.0.0.0 --port $PORT
"""

from fastapi import FastAPI

app = FastAPI(title="BC Route Planner")


@app.get("/")
def hello_world() -> str:
    return "Hello World"

@app.get("/meow")
def goodbye_world(n: int) -> str:
    return ("meow " * n).strip()