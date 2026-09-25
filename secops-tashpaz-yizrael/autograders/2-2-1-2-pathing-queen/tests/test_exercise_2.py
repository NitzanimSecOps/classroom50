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
def folder(tmp_path):
    """
    tmp_path/            (one flat folder -- the functions don't look inside subfolders)
    ├── notes.txt
    ├── todo.txt
    └── run.exe
    """
    make_file(tmp_path / "notes.txt")
    make_file(tmp_path / "todo.txt")
    make_file(tmp_path / "run.exe")
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
    def test_finds_file_in_folder(self, folder):
        """find() returns the path of a file that is in the folder."""
        assert find("todo.txt", folder) == folder / "todo.txt"

    def test_returns_none_when_not_found(self, folder):
        """find() returns None when no file in the folder has that name."""
        assert find("nonexistent.txt", folder) is None


class TestCountFilesByExtension:
    def test_counts_files_with_extension(self, folder):
        """count_files_by_extension() counts the .txt files in the folder (2 of them)."""
        assert count_files_by_extension(".txt", folder) == 2

    def test_returns_zero_when_no_matches(self, folder):
        """count_files_by_extension() returns 0 when no file has that extension."""
        assert count_files_by_extension(".pdf", folder) == 0


class TestGetLastEdited:
    def test_returns_most_recently_edited_file(self, tmp_path):
        """get_last_modified_file() returns the file that was modified last."""
        make_file(tmp_path / "old.txt", mtime=1_000_000)
        make_file(tmp_path / "newest.txt", mtime=3_000_000)
        make_file(tmp_path / "middle.txt", mtime=2_000_000)
        assert get_last_modified_file(tmp_path) == tmp_path / "newest.txt"

    def test_goes_by_time_not_by_name(self, tmp_path):
        """get_last_modified_file() goes by modification time, not by file name."""
        make_file(tmp_path / "zzz.txt", mtime=1_000_000)
        make_file(tmp_path / "aaa.exe", mtime=5_000_000)
        assert get_last_modified_file(tmp_path) == tmp_path / "aaa.exe"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
