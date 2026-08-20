import pytest
from solution import (
    BabyException,
    MommyDoesntAllowException,
    StinkyException,
    sum_list,
)


# ---------- Exception hierarchy ----------

def test_baby_is_an_exception():
    assert issubclass(BabyException, Exception)

def test_mommy_inherits_baby():
    assert issubclass(MommyDoesntAllowException, BabyException)

def test_stinky_inherits_baby():
    assert issubclass(StinkyException, BabyException)


# ---------- sum_list: normal cases ----------

def test_sum_normal():
    assert sum_list([1, 2, 3]) == 6

def test_sum_empty():
    assert sum_list([]) == 0

def test_sum_single():
    assert sum_list([100]) == 100

def test_near_thirteen_is_fine():
    # guards against over-triggering on values close to 13
    assert sum_list([6, 6]) == 12
    assert sum_list([14]) == 14


# ---------- sum_list: the cursed number ----------

def test_thirteen_in_list_raises():
    # 13 is present, total (18) is NOT 13 -> isolates the "element" rule
    with pytest.raises(MommyDoesntAllowException):
        sum_list([13, 5])

def test_total_equals_thirteen_raises():
    # no element is 13, but the sum is -> isolates the "total" rule
    with pytest.raises(MommyDoesntAllowException):
        sum_list([6, 7])

def test_total_thirteen_with_negatives():
    with pytest.raises(MommyDoesntAllowException):
        sum_list([20, -7])

def test_raised_error_is_catchable_as_baby():
    # inheritance must be functional, not just declared
    with pytest.raises(BabyException):
        sum_list([13])