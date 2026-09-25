import os
from pathlib import Path

import pytest

from solution import (
    count_files_by_extension,
    find,
    get_last_modified_file,
    join_paths,
)


def make_file(path: Path, mtime: float | None = None) -> Path:
    """Create a file (and any missing parents). Optionally pin its mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("content")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def tree(tmp_path):
    """
    tmp_path/
    ├── notes.txt
    ├── run.exe
    ├── sub/
    │   ├── report.txt
    │   └── deep/
    │       ├── target.txt
    │       └── archive.zip
    └── empty_dir/
    """
    make_file(tmp_path / "notes.txt")
    make_file(tmp_path / "run.exe")
    make_file(tmp_path / "sub" / "report.txt")
    make_file(tmp_path / "sub" / "deep" / "target.txt")
    make_file(tmp_path / "sub" / "deep" / "archive.zip")
    (tmp_path / "empty_dir").mkdir()
    return tmp_path


class TestJoinPaths:
    def test_joins_two_relative_paths(self):
        """join_paths(Path("a"), Path("b")) returns Path("a/b")."""
        assert join_paths(Path("a"), Path("b")) == Path("a") / "b"

    def test_joins_nested_paths(self):
        """join_paths joins longer paths: a/b + c/d -> a/b/c/d."""
        result = join_paths(Path("a") / "b", Path("c") / "d")
        assert result == Path("a") / "b" / "c" / "d"


class TestFind:
    def test_finds_file_deeply_nested(self, tree):
        """find() finds a file two folders deep (sub/deep/target.txt)."""
        assert find("target.txt", tree) == tree / "sub" / "deep" / "target.txt"

    def test_returns_none_when_not_found(self, tree):
        """find() returns None when no file has that name."""
        assert find("nonexistent.txt", tree) is None


class TestCountFilesByExtension:
    def test_counts_recursively_across_all_levels(self, tree):
        """count_files_by_extension() counts .txt files in every subfolder (3 of them)."""
        # notes.txt + sub/report.txt + sub/deep/target.txt
        assert count_files_by_extension(".txt", tree) == 3

    def test_returns_zero_when_no_matches(self, tree):
        """count_files_by_extension() returns 0 when no file has that extension."""
        assert count_files_by_extension(".pdf", tree) == 0


class TestGetLastEdited:
    def test_returns_most_recently_edited_file(self, tmp_path):
        """get_last_modified_file() returns the file that was modified last."""
        make_file(tmp_path / "old.txt", mtime=1_000_000)
        make_file(tmp_path / "newest.txt", mtime=3_000_000)
        make_file(tmp_path / "middle.txt", mtime=2_000_000)
        assert get_last_modified_file(tmp_path) == tmp_path / "newest.txt"

    def test_finds_newest_file_in_nested_directory(self, tmp_path):
        """get_last_modified_file() also looks inside subfolders."""
        make_file(tmp_path / "top.txt", mtime=1_000_000)
        make_file(tmp_path / "sub" / "deep" / "buried.txt", mtime=9_000_000)
        assert get_last_modified_file(tmp_path) == tmp_path / "sub" / "deep" / "buried.txt"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
