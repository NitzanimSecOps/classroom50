import os
from pathlib import Path

import pytest

from solutions.suite_2_2_1.exercise_2.solution import (
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
        assert join_paths(Path("a"), Path("b")) == Path("a") / "b"

    def test_joins_nested_paths(self):
        result = join_paths(Path("a") / "b", Path("c") / "d")
        assert result == Path("a") / "b" / "c" / "d"

    def test_returns_a_path_object(self):
        assert isinstance(join_paths(Path("a"), Path("b")), Path)

    def test_second_path_absolute_replaces_first(self, tmp_path):
        # pathlib's / operator: an absolute right-hand operand wins outright.
        # This is documented Path behavior, not an edge case you handle --
        # the test just pins it so a manual string-concat implementation
        # (which would produce "a/<abs>") gets caught.
        result = join_paths(Path("a"), tmp_path)
        assert result == tmp_path


class TestFind:
    def test_finds_file_in_base_directory(self, tree):
        assert find("notes.txt", tree) == tree / "notes.txt"

    def test_finds_file_one_level_deep(self, tree):
        assert find("report.txt", tree) == tree / "sub" / "report.txt"

    def test_finds_file_deeply_nested(self, tree):
        assert find("target.txt", tree) == tree / "sub" / "deep" / "target.txt"

    def test_returns_none_when_not_found(self, tree):
        assert find("nonexistent.txt", tree) is None

    def test_returns_none_in_empty_directory(self, tmp_path):
        assert find("anything.txt", tmp_path) is None

    def test_returned_path_actually_exists(self, tree):
        # Guards against building a path string that doesn't resolve.
        result = find("target.txt", tree)
        assert result is not None and result.is_file()


class TestCountFilesByExtension:
    def test_counts_recursively_across_all_levels(self, tree):
        # notes.txt + sub/report.txt + sub/deep/target.txt
        assert count_files_by_extension(".txt", tree) == 3

    def test_counts_single_match(self, tree):
        assert count_files_by_extension(".exe", tree) == 1

    def test_counts_match_in_nested_dir_only(self, tree):
        assert count_files_by_extension(".zip", tree) == 1

    def test_returns_zero_when_no_matches(self, tree):
        assert count_files_by_extension(".pdf", tree) == 0

    def test_returns_zero_for_empty_directory(self, tmp_path):
        assert count_files_by_extension(".txt", tmp_path) == 0

    def test_does_not_count_directories(self, tmp_path):
        # A directory named like a file must not be counted.
        (tmp_path / "tricky.txt").mkdir()
        make_file(tmp_path / "real.txt")
        assert count_files_by_extension(".txt", tmp_path) == 1

    def test_does_not_match_on_substring(self, tmp_path):
        # "notes.txt.bak" must not count as a .txt file.
        make_file(tmp_path / "notes.txt.bak")
        make_file(tmp_path / "real.txt")
        assert count_files_by_extension(".txt", tmp_path) == 1


class TestGetLastEdited:
    def test_returns_most_recently_edited_file(self, tmp_path):
        make_file(tmp_path / "old.txt", mtime=1_000_000)
        make_file(tmp_path / "newest.txt", mtime=3_000_000)
        make_file(tmp_path / "middle.txt", mtime=2_000_000)
        assert get_last_modified_file(tmp_path) == tmp_path / "newest.txt"

    def test_single_file_is_the_answer(self, tmp_path):
        make_file(tmp_path / "only.txt", mtime=1_000_000)
        assert get_last_modified_file(tmp_path) == tmp_path / "only.txt"

    def test_ignores_extension_and_name_ordering(self, tmp_path):
        # The alphabetically-first, "oldest-looking" name is the newest file.
        make_file(tmp_path / "zzz.txt", mtime=1_000_000)
        make_file(tmp_path / "aaa.exe", mtime=5_000_000)
        assert get_last_modified_file(tmp_path) == tmp_path / "aaa.exe"

    def test_returned_path_actually_exists(self, tmp_path):
        make_file(tmp_path / "a.txt", mtime=1_000_000)
        make_file(tmp_path / "b.txt", mtime=2_000_000)
        result = get_last_modified_file(tmp_path)
        assert result.is_file()

    def test_finds_newest_file_in_nested_directory(self, tmp_path):
        make_file(tmp_path / "top.txt", mtime=1_000_000)
        make_file(tmp_path / "sub" / "deep" / "buried.txt", mtime=9_000_000)
        assert get_last_modified_file(tmp_path) == tmp_path / "sub" / "deep" / "buried.txt"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])