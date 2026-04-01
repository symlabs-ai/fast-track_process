import pytest

from src.calculator import add, subtract, multiply, divide


class TestAdd:
    def test_add_positive_numbers(self):
        assert add(2, 3) == 5

    def test_add_negative_numbers(self):
        assert add(-1, -4) == -5

    def test_add_zero(self):
        assert add(0, 7) == 7


class TestSubtract:
    def test_subtract_positive_numbers(self):
        assert subtract(10, 3) == 7

    def test_subtract_resulting_negative(self):
        assert subtract(3, 10) == -7


class TestMultiply:
    def test_multiply_positive_numbers(self):
        assert multiply(4, 5) == 20

    def test_multiply_by_zero(self):
        assert multiply(9, 0) == 0


class TestDivide:
    def test_divide_exact(self):
        assert divide(10, 2) == 5

    def test_divide_float_result(self):
        assert divide(7, 2) == 3.5

    def test_divide_by_zero_raises(self):
        with pytest.raises(ValueError):
            divide(5, 0)
