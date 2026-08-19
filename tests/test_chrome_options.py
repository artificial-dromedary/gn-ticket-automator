"""Chrome's launch flags.

Headless Chrome is the largest allocation in the booking path, and on a 512 MB
instance it is what gets the container OOM-killed. These pin the memory-relevant
flags so a future edit cannot quietly drop one.
"""
import pytest

from gn_ticket import build_chrome_options


def args(**env):
    return build_chrome_options(headless_mode=env.pop("headless", True)).arguments


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("CHROME_WINDOW_SIZE", "CHROME_DISABLE_IMAGES",
                 "CHROME_JS_HEAP_MB", "CHROME_SINGLE_PROCESS",
                 "CHROME_DISABLE_SITE_ISOLATION"):
        monkeypatch.delenv(name, raising=False)


def test_the_viewport_is_small_by_default():
    """Raster memory scales with area; 1920x1080 costs more than twice 1280x720."""
    assert "--window-size=1280,720" in args()


def test_images_are_off_when_headless():
    assert "--blink-settings=imagesEnabled=false" in args()


def test_images_stay_on_when_watching_the_browser():
    """'Watch it work' is useless if the page renders blank."""
    assert "--blink-settings=imagesEnabled=false" not in args(headless=False)


def test_the_js_heap_is_capped():
    """A runaway page should fail as a renderer error, not take the container down."""
    assert "--js-flags=--max-old-space-size=256" in args()


def test_container_required_flags_are_present():
    flags = args()
    assert "--no-sandbox" in flags
    assert "--disable-dev-shm-usage" in flags
    assert "--headless" in flags


def test_caches_and_background_work_are_disabled():
    flags = args()
    for flag in ("--disk-cache-size=1", "--media-cache-size=1",
                 "--disable-background-networking", "--disable-sync",
                 "--disable-component-update", "--metrics-recording-only"):
        assert flag in flags


def test_single_process_is_off_unless_asked_for(monkeypatch):
    assert "--single-process" not in args()

    monkeypatch.setenv("CHROME_SINGLE_PROCESS", "1")
    assert "--single-process" in args()


def test_every_lever_is_tunable_without_a_deploy(monkeypatch):
    monkeypatch.setenv("CHROME_WINDOW_SIZE", "1024,768")
    monkeypatch.setenv("CHROME_JS_HEAP_MB", "192")
    monkeypatch.setenv("CHROME_DISABLE_IMAGES", "false")

    flags = args()
    assert "--window-size=1024,768" in flags
    assert "--js-flags=--max-old-space-size=192" in flags
    assert "--blink-settings=imagesEnabled=false" not in flags


def test_the_heap_cap_can_be_removed(monkeypatch):
    monkeypatch.setenv("CHROME_JS_HEAP_MB", "0")

    assert not any(f.startswith("--js-flags") for f in args())


def test_site_isolation_is_off_by_default():
    """Each isolated origin is its own renderer process; the browser visits one
    trusted site, so those processes cost memory and guard against nothing."""
    flags = args()

    assert "--disable-features=IsolateOrigins,site-per-process" in flags
    assert "--disable-site-isolation-trials" in flags


def test_site_isolation_can_be_restored(monkeypatch):
    monkeypatch.setenv("CHROME_DISABLE_SITE_ISOLATION", "false")
    flags = args()

    assert "--disable-features=IsolateOrigins,site-per-process" not in flags
    assert "--disable-site-isolation-trials" not in flags
