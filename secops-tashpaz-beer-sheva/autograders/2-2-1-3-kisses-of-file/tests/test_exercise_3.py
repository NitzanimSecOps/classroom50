from datetime import date
from pathlib import Path

import pytest

from solution import find_player, print_players, write_diary

PLAYERS = """\
Yossi Benayoun, Hapoel Be'er Sheva, 05/05/1980, Kashar
Eran Zahavi, Hapoel Tel Aviv, 25/07/1987, Halutz
Bibras Natcho, Hapoel Tel Aviv, 18/02/1988, Kashar
Tal Ben Haim, Maccabi Petah Tikva, 31/03/1989, Hagan
Ramzi Safuri, Hapoel Be'er Sheva, 21/10/1995, Kashar
"""


@pytest.fixture
def players_file(tmp_path: Path) -> Path:
    path = tmp_path / "players.txt"
    path.write_text(PLAYERS, encoding="utf-8")
    return path


# --------------------------- write_diary ---------------------------


def test_write_diary_returns_false_when_diary_is_missing(tmp_path: Path) -> None:
    """write_diary() returns False when the diary file doesn't exist."""
    missing = tmp_path / "no_such_diary.txt"

    assert write_diary(missing, "Dear diary") is False


def test_write_diary_does_not_create_a_missing_diary(tmp_path: Path) -> None:
    """write_diary() doesn't create a diary file that doesn't exist."""
    missing = tmp_path / "no_such_diary.txt"

    write_diary(missing, "Dear diary")

    assert not missing.exists()


def test_write_diary_writes_date_then_message(tmp_path: Path) -> None:
    """write_diary() writes today's date, then the message on the next line, and returns True."""
    diary = tmp_path / "diary.txt"
    diary.write_text("", encoding="utf-8")

    assert write_diary(diary, "Trained today") is True

    assert diary.read_text(encoding="utf-8") == f"{date.today()}\nTrained today\n"


def test_write_diary_appends_instead_of_overwriting(tmp_path: Path) -> None:
    """write_diary() adds to the end of the diary instead of erasing what's already there."""
    diary = tmp_path / "diary.txt"
    diary.write_text("Previous entry\n", encoding="utf-8")

    write_diary(diary, "New entry")

    lines = diary.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "Previous entry"
    assert lines[-1] == "New entry"


def test_write_diary_keeps_every_entry(tmp_path: Path) -> None:
    """write_diary() keeps every entry when called several times."""
    diary = tmp_path / "diary.txt"
    diary.write_text("", encoding="utf-8")

    write_diary(diary, "First")
    write_diary(diary, "Second")

    assert diary.read_text(encoding="utf-8").count(str(date.today())) == 2


# --------------------------- print_players ---------------------------


def test_print_players_formats_each_player(players_file: Path, capsys) -> None:
    """print_players() prints each player as Name / Team / D.O.B. / Position lines."""
    print_players(players_file)

    output = capsys.readouterr().out
    assert "Name: Yossi Benayoun" in output
    assert "Team: Hapoel Be'er Sheva" in output
    assert "D.O.B.: 05/05/1980" in output
    assert "Position: Kashar" in output


def test_print_players_prints_every_player(players_file: Path, capsys) -> None:
    """print_players() prints every player in the file (all 5)."""
    print_players(players_file)

    output = capsys.readouterr().out
    assert output.count("Name: ") == 5


def test_print_players_returns_the_parsed_players(players_file: Path) -> None:
    """print_players() returns a list of players, each a list of its 4 fields."""
    players = print_players(players_file)

    assert len(players) == 5
    assert players[0] == ["Yossi Benayoun", "Hapoel Be'er Sheva", "05/05/1980", "Kashar"]


def test_print_players_strips_the_trailing_newline(players_file: Path) -> None:
    """print_players() removes the newline at the end of each line (the last field is just Kashar)."""
    players = print_players(players_file)

    # the last field must not carry a "\n" from the file
    assert players[0][-1] == "Kashar"


# --------------------------- find_player ---------------------------


def test_find_player_returns_the_player_fields(players_file: Path) -> None:
    """find_player() returns the player's 4 fields as a list."""
    assert find_player(players_file, "Bibras Natcho") == [
        "Bibras Natcho",
        "Hapoel Tel Aviv",
        "18/02/1988",
        "Kashar",
    ]


def test_find_player_finds_the_first_line(players_file: Path) -> None:
    """find_player() finds the player on the first line of the file."""
    assert find_player(players_file, "Yossi Benayoun")[1] == "Hapoel Be'er Sheva"


def test_find_player_finds_the_last_line(players_file: Path) -> None:
    """find_player() finds the player on the last line of the file."""
    assert find_player(players_file, "Ramzi Safuri")[3] == "Kashar"


def test_find_player_returns_empty_list_when_missing(players_file: Path) -> None:
    """find_player() returns [] when the player isn't in the file."""
    assert find_player(players_file, "Lionel Messi") == []


def test_find_player_does_not_match_a_partial_name(players_file: Path) -> None:
    """find_player() needs the full name: "Tal" doesn't match "Tal Ben Haim"."""
    # "Tal Ben Haim" is in the file; "Tal" alone must not match it
    assert find_player(players_file, "Tal") == []


def test_find_player_does_not_match_a_team_name(players_file: Path) -> None:
    """find_player() searches names only: a team name doesn't match."""
    # the name must be matched against the name field only, not the whole line
    assert find_player(players_file, "Hapoel Tel Aviv") == []