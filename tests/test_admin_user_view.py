"""The admin console links to the user dashboard without breaking its section tabs."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "admin.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "admin.js").read_text(encoding="utf-8")


def _link_tag():
    match = re.search(r"<a [^>]*href=\"/app\"[^>]*>", HTML)
    assert match, "the admin console has no link to /app"
    return match.group(0)


def test_admin_nav_links_to_the_user_dashboard_in_a_new_tab():
    tag = _link_tag()
    assert 'class="tab-link"' in tag
    assert 'target="_blank"' in tag and 'rel="noopener"' in tag


def test_the_user_dashboard_link_is_not_a_section_tab():
    # admin.js switches sections for every element with class "tab"; the link must not have it.
    assert 'class="tab"' not in _link_tag()
    assert 'querySelectorAll(".tab")' in JS
