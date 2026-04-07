"""String utility functions for text transformations."""

import re


def to_camel_case(text: str) -> str:
    """
    Convert a string to camelCase.

    Handles snake_case, hyphens, spaces, and multiple delimiters.
    Keeps the first character lowercase.

    Args:
        text: Input string (e.g., "hello_world", "hello-world", "hello world")

    Returns:
        camelCase formatted string (e.g., "helloWorld")

    Examples:
        >>> to_camel_case("hello_world")
        'helloWorld'
        >>> to_camel_case("hello-world")
        'helloWorld'
        >>> to_camel_case("hello world")
        'helloWorld'
        >>> to_camel_case("HELLO_WORLD")
        'helloWorld'
        >>> to_camel_case("variable1Name")
        'variable1Name'
        >>> to_camel_case("test-2-value_3")
        'test2Value3'
    """
    if not text:
        return text

    # Replace multiple types of delimiters (underscore, hyphen, space) with space
    text = re.sub(r'[_\-\s]+', ' ', text)

    # Split by space to get words
    words = text.split()

    if not words:
        return ""

    # First word stays lowercase, capitalize first letter of remaining words
    result = words[0].lower()
    for word in words[1:]:
        if word:  # Skip empty strings
            # Remove any non-alphanumeric characters within the word
            word = re.sub(r'[^a-zA-Z0-9]', '', word)
            if word:
                result += word[0].upper() + word[1:].lower()

    return result
