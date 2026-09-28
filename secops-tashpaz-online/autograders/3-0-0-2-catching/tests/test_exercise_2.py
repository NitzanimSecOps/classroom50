import pytest
from solution import divide_numbers, grab


# ---------- Exercise 2: divide_numbers ----------

def test_divide_normal():
    assert divide_numbers(10, 2) == 5.0

def test_divide_returns_float():
    result = divide_numbers(7, 2)
    assert isinstance(result, float)
    assert result == 3.5

def test_divide_negative():
    assert divide_numbers(-10, 2) == -5.0

def test_divide_by_zero_returns_zero_float():
    result = divide_numbers(5, 0)
    assert result == 0.0
    assert isinstance(result, float)   # not int 0 — 0 == 0.0 would pass otherwise


# ---------- Exercise 3: grab ----------

def test_grab_normal():
    assert grab(["10", "20", "30"], 1) == 20

def test_grab_returns_int():
    assert isinstance(grab(["1", "2", "3"], 0), int)

def test_grab_index_out_of_range(capsys):
    result = grab(["10", "20", "30"], 99)   # IndexError
    printed = capsys.readouterr().out
    assert result == 0
    assert printed.strip() != ""            # must report the problem — text is theirs

def test_grab_not_a_number(capsys):
    result = grab(["10", "hello", "30"], 1)  # ValueError from int("hello")
    printed = capsys.readouterr().out
    assert result == 0
    assert printed.strip() != ""
    