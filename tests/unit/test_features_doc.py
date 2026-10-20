"""Keep docs/FEATURES.md machine-checkable: every row has Done = true|false, IDs are
unique, and the status line's counts match the table."""

import re

from tests.conftest import REPO

FEATURES = REPO / "docs" / "FEATURES.md"
ROW = re.compile(r"^\| (F\d+\.\d+) \|.*\| (P[012]) \| (\S+) \|$")


def rows() -> list[tuple[str, str, str]]:
    found = []
    for line in FEATURES.read_text().splitlines():
        if re.match(r"^\| F\d+\.\d+ ", line):
            m = ROW.match(line)
            assert m, f"malformed feature row: {line[:80]}"
            found.append((m.group(1), m.group(2), m.group(3)))
    return found


def test_done_values_are_true_or_false() -> None:
    bad = [(fid, done) for fid, _, done in rows() if done not in {"true", "false"}]
    assert not bad


def test_feature_ids_are_unique() -> None:
    ids = [fid for fid, _, _ in rows()]
    assert len(ids) == len(set(ids))


def test_status_line_matches_table() -> None:
    r = rows()
    done = sum(1 for _, _, d in r if d == "true")
    m = re.search(r"\*\*Status \([\d-]+\):\*\* (\d+) of (\d+) done", FEATURES.read_text())
    assert m, "status line missing"
    assert (int(m.group(1)), int(m.group(2))) == (done, len(r))
