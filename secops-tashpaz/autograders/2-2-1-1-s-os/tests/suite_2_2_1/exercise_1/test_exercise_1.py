# -*- coding: utf-8 -*-
"""
Tests for the OS-integration assignment:
    get_logged_in_user() -> str
    get_user_directory() -> list
    make_user_folder(base_path: str) -> bool
    relative_to_absolute(relative_path: str) -> str
"""
import os
import pytest
from unittest.mock import patch

from solutions.suite_2_2_1.exercise_1.solution import (
    get_logged_in_user,
    get_user_directory,
    make_user_folder,
    relative_to_absolute,
)

SOLUTION_MODULE = "solutions.suite_2_2_1.exercise_1.solution"


class TestGetLoggedInUser:
    @patch(f"{SOLUTION_MODULE}.os.getlogin", return_value="shuro")
    def test_returns_os_getlogin_result(self, mock_getlogin):
        assert get_logged_in_user() == "shuro"
        mock_getlogin.assert_called_once()


class TestGetUserDirectory:
    @patch(f"{SOLUTION_MODULE}.os.listdir")
    @patch(f"{SOLUTION_MODULE}.os.path.expanduser", return_value=os.path.join("Users_root", "current_user"))
    @patch(f"{SOLUTION_MODULE}.get_logged_in_user", return_value="shuro")
    def test_lists_files_in_logged_in_users_home_directory(self, mock_user, mock_expanduser, mock_listdir):
        mock_listdir.return_value = ["Desktop", "Documents", "Downloads"]
        result = get_user_directory()
        assert result == ["Desktop", "Documents", "Downloads"]
        mock_listdir.assert_called_once_with(os.path.join("Users_root", "shuro"))

    @patch(f"{SOLUTION_MODULE}.os.listdir")
    @patch(f"{SOLUTION_MODULE}.os.path.expanduser", return_value=os.path.join("Users_root", "current_user"))
    @patch(f"{SOLUTION_MODULE}.get_logged_in_user", return_value="alex")
    def test_uses_current_logged_in_user_not_a_hardcoded_name(self, mock_user, mock_expanduser, mock_listdir):
        get_user_directory()
        mock_listdir.assert_called_once_with(os.path.join("Users_root", "alex"))


class TestMakeUserFolder:
    """
    Uses tmp_path (a real, unique, auto-cleaned temp directory) so these
    tests exercise the real filesystem and don't assume which os.* call
    you used to check existence.
    """

    @patch(f"{SOLUTION_MODULE}.get_logged_in_user", return_value="shuro")
    def test_creates_folder_and_returns_true(self, mock_user, tmp_path):
        result = make_user_folder(str(tmp_path))
        assert result is True
        assert (tmp_path / "shuros_cool_folder").is_dir()

    @patch(f"{SOLUTION_MODULE}.get_logged_in_user", return_value="shuro")
    def test_existing_target_folder_returns_false(self, mock_user, tmp_path):
        (tmp_path / "shuros_cool_folder").mkdir()
        result = make_user_folder(str(tmp_path))
        assert result is False
        # still there, wasn't touched/recreated in a way that would error
        assert (tmp_path / "shuros_cool_folder").is_dir()

    @patch(f"{SOLUTION_MODULE}.get_logged_in_user", return_value="shuro")
    def test_nonexistent_base_path_returns_false(self, mock_user, tmp_path):
        missing_base = tmp_path / "does_not_exist"
        result = make_user_folder(str(missing_base))
        assert result is False
        # nothing should have been created anywhere
        assert not missing_base.exists()

    @patch(f"{SOLUTION_MODULE}.get_logged_in_user", return_value="alex")
    def test_folder_name_uses_current_user(self, mock_user, tmp_path):
        result = make_user_folder(str(tmp_path))
        assert result is True
        assert (tmp_path / "alexs_cool_folder").is_dir()


class TestRelativeToAbsolute:
    """
    Uses a real changed cwd (monkeypatch.chdir) and a real absolute path
    (tmp_path), so these tests don't assume you used os.path.isabs() or
    os.getcwd() specifically -- only that the documented behavior holds.
    """

    def test_relative_path_resolved_against_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        expected = os.path.join(str(tmp_path), "subfolder")
        assert relative_to_absolute("subfolder") == expected

    def test_nested_relative_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        nested = os.path.join("a", "b", "c")
        expected = os.path.join(str(tmp_path), "a", "b", "c")
        assert relative_to_absolute(nested) == expected

    def test_already_absolute_path_returned_unchanged(self, tmp_path):
        absolute = str(tmp_path)
        assert relative_to_absolute(absolute) == absolute


if __name__ == "__main__":
    pytest.main([__file__, "-v"])