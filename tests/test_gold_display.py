"""Gold (and every market) shows in the scan table: live rows, or an UNAVAILABLE row when data is missing."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _app_js() -> str:
    return (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def test_scan_table_lists_unavailable_markets_as_rows():
    js = _app_js()
    assert "SCAN.unavailable" in js
    assert "function unavailableRow" in js
    assert 'side: "UNAVAILABLE"' in js
    # the table and the count use the combined list, not only signals + watchlist
    assert "const listed = [...all, ...(SCAN.unavailable || []).map(unavailableRow)]" in js
    assert "listed.map(" in js or "tbody.innerHTML = listed" in js


def test_unavailable_rows_keep_the_click_through_to_the_pair_view():
    js = _app_js()
    # clicking any row, including an unavailable one, opens the pair view that explains the gap
    assert "loadPair(tr.dataset.symbol)" in js
    assert 'PAIR.status === "unavailable"' in js


def test_publisher_writes_the_unavailable_list_into_the_scan_row():
    src = (ROOT / "fxbot" / "publish.py").read_text(encoding="utf-8")
    assert re.search(r'report\["unavailable"\]\s*=', src)
    assert 'upsert_statement("scan"' in src
