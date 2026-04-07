"""Unit tests for to_camel_case function."""

import pytest
from src.utils.string_utils import to_camel_case


class TestToCamelCase:
    """Test cases for to_camel_case conversion."""

    def test_snake_case_conversion(self):
        """Test basic snake_case to camelCase conversion."""
        assert to_camel_case("hello_world") == "helloWorld"
        assert to_camel_case("my_variable_name") == "myVariableName"
        assert to_camel_case("simple") == "simple"

    def test_hyphen_conversion(self):
        """Test kebab-case (hyphen-separated) to camelCase conversion."""
        assert to_camel_case("hello-world") == "helloWorld"
        assert to_camel_case("my-variable-name") == "myVariableName"
        assert to_camel_case("test-case-here") == "testCaseHere"

    def test_space_conversion(self):
        """Test space-separated strings to camelCase conversion."""
        assert to_camel_case("hello world") == "helloWorld"
        assert to_camel_case("multiple words here") == "multipleWordsHere"
        assert to_camel_case("a b c") == "aBC"

    def test_numeric_handling(self):
        """Test handling of numbers and mixed alphanumeric strings."""
        assert to_camel_case("variable1_name") == "variable1Name"
        assert to_camel_case("test-2-value_3") == "test2Value3"
        assert to_camel_case("html2_pdf") == "html2Pdf"
        assert to_camel_case("error_404_page") == "error404Page"

    def test_mixed_delimiters(self):
        """Test handling of mixed delimiters."""
        assert to_camel_case("hello_world-test case") == "helloWorldTestCase"
        assert to_camel_case("my__variable--name") == "myVariableName"

    def test_uppercase_handling(self):
        """Test handling of uppercase input."""
        assert to_camel_case("HELLO_WORLD") == "helloWorld"
        assert to_camel_case("HELLO-WORLD") == "helloWorld"

    def test_edge_cases(self):
        """Test edge cases."""
        assert to_camel_case("") == ""
        assert to_camel_case("_") == ""
        assert to_camel_case("__") == ""
        assert to_camel_case("_test_") == "test"
        assert to_camel_case("a") == "a"
        assert to_camel_case("A") == "a"

    def test_multiple_consecutive_delimiters(self):
        """Test multiple consecutive delimiters are normalized."""
        assert to_camel_case("hello___world") == "helloWorld"
        assert to_camel_case("hello---world") == "helloWorld"
        assert to_camel_case("hello   world") == "helloWorld"
        assert to_camel_case("hello_--_world") == "helloWorld"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
