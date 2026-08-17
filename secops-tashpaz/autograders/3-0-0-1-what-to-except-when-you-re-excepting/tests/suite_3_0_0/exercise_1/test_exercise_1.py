# -*- coding: utf-8 -*-
"""
Tests for the exception-generator assignment:
    create_zero_division_error()
    create_key_error()
    create_recursion_error()
    create_type_error()
    create_index_error()
    create_file_not_found_error()
    create_name_error()

Each function should raise exactly the exception type its name promises.
"""
import pytest

from solutions.suite_3_0_0.exercise_1.exception_generator import (
    create_zero_division_error,
    create_key_error,
    create_recursion_error,
    create_type_error,
    create_index_error,
    create_file_not_found_error,
    create_name_error,
)


def test_create_zero_division_error_raises_zero_division_error():
    with pytest.raises(ZeroDivisionError):
        create_zero_division_error()


def test_create_key_error_raises_key_error():
    with pytest.raises(KeyError):
        create_key_error()


def test_create_recursion_error_raises_recursion_error():
    with pytest.raises(RecursionError):
        create_recursion_error()


def test_create_type_error_raises_type_error():
    with pytest.raises(TypeError):
        create_type_error()


def test_create_index_error_raises_index_error():
    with pytest.raises(IndexError):
        create_index_error()


def test_create_file_not_found_error_raises_file_not_found_error():
    with pytest.raises(FileNotFoundError):
        create_file_not_found_error()


def test_create_name_error_raises_name_error():
    with pytest.raises(NameError):
        create_name_error()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
