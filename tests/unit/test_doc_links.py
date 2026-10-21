"""Every relative markdown link in the repo's docs must point at a file that exists."""

import re
from pathlib import Path

from tests.conftest import REPO

LINK = re.compile(r"\]\(([^)\s]+)\)")
DOCS = [REPO / "README.md", REPO / "CLAUDE.md", *sorted((REPO / "docs").rglob("*.md"))]


def broken_links(path: Path) -> list[str]:
    text = path.read_text()
    # links inside fenced code blocks are examples, not links
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    bad = []
    for target in LINK.findall(text):
        if re.match(r"^[a-z]+:", target) or target.startswith("#"):
            continue  # external URL, mailto, or in-page anchor
        file_part = target.split("#", 1)[0]
        if "<" in file_part:
            continue  # template placeholder such as M<n>_<TOPIC>_RESULTS.md
        if not (path.parent / file_part).exists():
            bad.append(target)
    return bad


def test_relative_doc_links_resolve() -> None:
    problems = {str(p.relative_to(REPO)): b for p in DOCS if (b := broken_links(p))}
    assert not problems
