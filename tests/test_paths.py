"""The backend modules live in backend/ but read the repo-root data/ folder. Every other
test points these constants at a tmp_path, so this is the one place that checks the real
locations resolve -- e.g. after moving a module, Path(__file__).parent would silently
point at backend/data/."""

import data_download
import fetch_gacha_units
import gacha_units
import unit_rarity
from paths import DATA_DIR, PROJECT_ROOT


def test_data_dir_is_the_repo_root_data_folder():
    assert (PROJECT_ROOT / "backend" / "paths.py").is_file()
    assert DATA_DIR == PROJECT_ROOT / "data"


def test_committed_data_folders_are_found():
    for path in [gacha_units.GACHA_POOLS_DIR, fetch_gacha_units.GACHA_POOLS_DIR,
                 fetch_gacha_units.ICONS_DIR, unit_rarity.UNIT_DATA_DIR]:
        assert path.is_dir(), path
        assert path.is_relative_to(DATA_DIR)


def test_seed_tracks_is_under_data():
    # gitignored, so it may not exist yet on a fresh clone; only the location is checked
    assert data_download.SEED_TRACKS_DIR == DATA_DIR / "seed_tracks"
