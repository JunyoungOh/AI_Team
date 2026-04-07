"""Tests for string utility functions."""

import pytest
from src.utils.string_utils import to_camel_case


class TestToCamelCase:
    """Test cases for to_camel_case function."""

    def test_snake_case_basic(self):
        """Test basic snake_case conversion."""
        assert to_camel_case("hello_world") == "helloWorld"

    def test_snake_case_multiple_words(self):
        """Test snake_case with multiple words."""
        assert to_camel_case("my_variable_name") == "myVariableName"

    def test_space_separated(self):
        """Test space-separated words."""
        assert to_camel_case("hello world") == "helloWorld"

    def test_space_separated_multiple_words(self):
        """Test multiple space-separated words."""
        assert to_camel_case("my variable name") == "myVariableName"

    def test_uppercase_input(self):
        """Test uppercase input is lowercased appropriately."""
        assert to_camel_case("HELLO_WORLD") == "helloWorld"

    def test_mixed_case_input(self):
        """Test mixed case input."""
        assert to_camel_case("Hello_World") == "helloWorld"

    def test_single_word(self):
        """Test single word returns lowercase."""
        assert to_camel_case("hello") == "hello"

    def test_single_word_uppercase(self):
        """Test single uppercase word is lowercased."""
        assert to_camel_case("HELLO") == "hello"

    def test_empty_string(self):
        """Test empty string returns empty."""
        assert to_camel_case("") == ""

    def test_only_underscores(self):
        """Test string with only underscores."""
        assert to_camel_case("_") == ""

    def test_multiple_underscores_between_words(self):
        """Test multiple underscores between words."""
        assert to_camel_case("hello__world") == "helloWorld"

    def test_leading_underscore(self):
        """Test leading underscore."""
        assert to_camel_case("_hello_world") == "helloWorld"

    def test_trailing_underscore(self):
        """Test trailing underscore."""
        assert to_camel_case("hello_world_") == "helloWorld"

    def test_mixed_delimiters(self):
        """Test both spaces and underscores."""
        assert to_camel_case("hello_world test") == "helloWorldTest"

    def test_multiple_spaces(self):
        """Test multiple consecutive spaces."""
        assert to_camel_case("hello  world") == "helloWorld"
