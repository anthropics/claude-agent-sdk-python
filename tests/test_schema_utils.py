"""Tests for schema utilities."""

import pytest

from claude_agent_sdk._internal.schema_utils import (
    convert_output_format,
    is_pydantic_model,
    pydantic_to_json_schema,
)

# Try to import pydantic
try:
    from pydantic import BaseModel

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    BaseModel = None  # type: ignore


@pytest.mark.skipif(not PYDANTIC_AVAILABLE, reason="Pydantic not installed")
class TestPydanticConversion:
    """Test Pydantic model conversion to JSON schema."""

    def test_is_pydantic_model_with_model(self):
        """Test is_pydantic_model with a Pydantic model."""

        class TestModel(BaseModel):  # type: ignore
            name: str
            age: int

        assert is_pydantic_model(TestModel) is True

    def test_is_pydantic_model_with_non_model(self):
        """Test is_pydantic_model with non-Pydantic objects."""
        assert is_pydantic_model(dict) is False
        assert is_pydantic_model(str) is False
        assert is_pydantic_model("not a class") is False
        assert is_pydantic_model(123) is False

    def test_pydantic_to_json_schema_basic(self):
        """Test converting a basic Pydantic model to JSON schema."""

        class EmailExtraction(BaseModel):  # type: ignore
            name: str
            email: str
            plan_interest: str
            demo_requested: bool

        schema = pydantic_to_json_schema(EmailExtraction)

        # Verify it's a valid JSON schema
        assert isinstance(schema, dict)
        assert "type" in schema
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "name" in schema["properties"]
        assert "email" in schema["properties"]
        assert "plan_interest" in schema["properties"]
        assert "demo_requested" in schema["properties"]

    def test_pydantic_to_json_schema_nested(self):
        """Test converting a nested Pydantic model to JSON schema."""

        class Address(BaseModel):  # type: ignore
            street: str
            city: str

        class Person(BaseModel):  # type: ignore
            name: str
            address: Address

        schema = pydantic_to_json_schema(Person)

        assert isinstance(schema, dict)
        assert "properties" in schema
        assert "name" in schema["properties"]
        assert "address" in schema["properties"]

    def test_pydantic_to_json_schema_not_a_model(self):
        """Test that non-Pydantic objects raise TypeError."""
        with pytest.raises(TypeError, match="Expected a Pydantic model"):
            pydantic_to_json_schema(dict)  # type: ignore

    def test_convert_output_format_with_pydantic_model(self):
        """Test convert_output_format with a Pydantic model."""

        class TestModel(BaseModel):  # type: ignore
            name: str
            value: int

        result = convert_output_format(TestModel)

        assert result is not None
        assert result["type"] == "json_schema"
        assert "schema" in result
        assert isinstance(result["schema"], dict)
        assert result["schema"]["type"] == "object"


class TestConvertOutputFormat:
    """Test output format conversion."""

    def test_convert_output_format_with_none(self):
        """Test convert_output_format with None."""
        result = convert_output_format(None)
        assert result is None

    def test_convert_output_format_with_raw_schema(self):
        """Test convert_output_format with a raw JSON schema."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }

        result = convert_output_format(schema)

        assert result is not None
        assert result["type"] == "json_schema"
        assert result["schema"] == schema

    def test_convert_output_format_with_full_format(self):
        """Test convert_output_format with already formatted dict."""
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        full_format = {"type": "json_schema", "schema": schema}

        result = convert_output_format(full_format)

        assert result == full_format

    def test_convert_output_format_with_invalid_type(self):
        """Test convert_output_format with invalid type in full format."""
        invalid_format = {
            "type": "invalid_type",
            "schema": {"type": "object"},
        }

        with pytest.raises(ValueError, match="Invalid output_format type"):
            convert_output_format(invalid_format)

    def test_convert_output_format_with_invalid_object(self):
        """Test convert_output_format with invalid object type."""
        with pytest.raises(TypeError, match="output_format must be a dict"):
            convert_output_format(123)  # type: ignore

        with pytest.raises(TypeError, match="output_format must be a dict"):
            convert_output_format("not a dict")  # type: ignore


@pytest.mark.skipif(
    PYDANTIC_AVAILABLE, reason="Test requires Pydantic to be unavailable"
)
class TestWithoutPydantic:
    """Test behavior when Pydantic is not installed."""

    def test_is_pydantic_model_without_pydantic(self):
        """Test is_pydantic_model returns False when Pydantic is not installed."""
        assert is_pydantic_model(dict) is False

    def test_pydantic_to_json_schema_without_pydantic(self):
        """Test that pydantic_to_json_schema raises ImportError."""
        with pytest.raises(ImportError, match="Pydantic is not installed"):
            pydantic_to_json_schema(dict)  # type: ignore
