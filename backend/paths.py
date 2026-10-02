"""Where the repo-level data/ folder is. Backend modules live in backend/ but data/ sits
at the repo root (shared reference data, committed; plus the gitignored seed_tracks)."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
