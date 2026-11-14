from pathlib import Path

import pytest

from packages.inspector import BrowserInspector


@pytest.mark.asyncio
async def test_probe_snapshot_and_selector_test():
    inspector = BrowserInspector(max_sessions=1)
    url = (Path(__file__).parents[1] / "websites" / "list.html").resolve().as_uri()
    try:
        try:
            opened = await inspector.open(url)
        except Exception as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Playwright Chromium is not installed")
            raise
        snapshot = await inspector.snapshot(opened["session_id"])
        result = await inspector.selector_test(
            opened["session_id"], "xpath", "//article[contains(@class,'news-item')]"
        )
        assert "Alpha launch" in snapshot["visible_text"]
        assert result["valid"] is True
        assert result["match_count"] == 2
    finally:
        await inspector.shutdown()

