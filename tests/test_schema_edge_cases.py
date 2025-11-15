"""Edge case tests for schema utilities."""

import json

import pytest

from claude_agent_sdk._internal.schema_utils import (
    _clean_schema_for_anthropic,
    convert_output_format,
    is_pydantic_model,
    pydantic_to_json_schema,
)

# Try to import pydantic
try:
    from pydantic import BaseModel, Field

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    BaseModel = None  # type: ignore


@pytest.mark.skipif(not PYDANTIC_AVAILABLE, reason="Pydantic not installed")
class TestSchemaEdgeCases:
    """Test edge cases in schema conversion."""

    def test_nested_model_references(self):
        """Test that nested models preserve $ref structure."""

        class Address(BaseModel):  # type: ignore
            street: str
            city: str

        class Person(BaseModel):  # type: ignore
            name: str
            address: Address

        schema = pydantic_to_json_schema(Person)

        # Should have $defs section
        assert "$defs" in schema or "definitions" in schema
        # Address should be in properties
        assert "address" in schema["properties"]

    def test_optional_fields_with_none_default(self):
        """Test optional fields with None default value."""

        class Model(BaseModel):  # type: ignore
            required_field: str
            optional_field: str | None = None

        schema = pydantic_to_json_schema(Model)

        # Required should only include required_field
        assert "required_field" in schema["required"]
        assert "optional_field" not in schema["required"]

    def test_list_fields(self):
        """Test list field conversion."""

        class Model(BaseModel):  # type: ignore
            items: list[str]
            numbers: list[int]

        schema = pydantic_to_json_schema(Model)

        assert schema["properties"]["items"]["type"] == "array"
        assert schema["properties"]["numbers"]["type"] == "array"

    def test_field_with_description(self):
        """Test that field descriptions are preserved."""

        class Model(BaseModel):  # type: ignore
            name: str = Field(description="The user's name")

        schema = pydantic_to_json_schema(Model)

        assert "description" in schema["properties"]["name"]
        assert schema["properties"]["name"]["description"] == "The user's name"

    def test_field_with_constraints(self):
        """Test that field constraints are preserved."""

        class Model(BaseModel):  # type: ignore
            age: int = Field(ge=0, le=120)
            score: float = Field(gt=0.0, lt=100.0)

        schema = pydantic_to_json_schema(Model)

        # Age should have minimum and maximum
        age_schema = schema["properties"]["age"]
        assert "minimum" in age_schema or "exclusiveMinimum" in age_schema

        # Score should have exclusiveMinimum and exclusiveMaximum
        score_schema = schema["properties"]["score"]
        assert "maximum" in score_schema or "exclusiveMaximum" in score_schema

    def test_model_with_class_docstring(self):
        """Test that model docstrings become descriptions."""

        class Model(BaseModel):  # type: ignore
            """This is a test model."""

            value: str

        schema = pydantic_to_json_schema(Model)

        # Should have description from docstring
        assert "description" in schema
        assert "test model" in schema["description"].lower()

    def test_clean_schema_removes_pydantic_metadata(self):
        """Test that _clean_schema_for_anthropic removes unnecessary fields."""
        raw_schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "title": "MyModel",
            "properties": {"name": {"type": "string"}},
            "definitions": {
                "SubModel": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                }
            },
        }

        cleaned = _clean_schema_for_anthropic(raw_schema)

        # $schema should be removed
        assert "$schema" not in cleaned
        # But title and properties should remain
        assert "title" in cleaned
        assert "properties" in cleaned

    def test_clean_schema_preserves_refs(self):
        """Test that schemas with $ref keep their definitions."""
        raw_schema = {
            "type": "object",
            "properties": {"address": {"$ref": "#/$defs/Address"}},
            "$defs": {
                "Address": {
                    "type": "object",
                    "properties": {"street": {"type": "string"}},
                }
            },
        }

        cleaned = _clean_schema_for_anthropic(raw_schema)

        # Should keep $defs because there's a $ref
        assert "$defs" in cleaned

    def test_convert_output_format_with_already_wrapped_schema(self):
        """Test that already-wrapped schemas are not double-wrapped."""
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        wrapped = {"type": "json_schema", "schema": schema}

        result = convert_output_format(wrapped)

        assert result == wrapped
        # Should not be double-wrapped
        assert result["type"] == "json_schema"
        assert "schema" in result
        assert result["schema"] == schema

    def test_convert_output_format_wraps_raw_schema(self):
        """Test that raw schemas get wrapped properly."""
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}

        result = convert_output_format(schema)

        assert result["type"] == "json_schema"
        assert result["schema"] == schema

    def test_convert_output_format_rejects_invalid_type(self):
        """Test that invalid type in wrapped format raises error."""
        invalid = {
            "type": "xml_schema",  # Invalid type
            "schema": {"type": "object"},
        }

        with pytest.raises(ValueError, match="Invalid output_format type"):
            convert_output_format(invalid)

    def test_pydantic_v2_compatibility(self):
        """Test that the code works with Pydantic v2 features."""
        # This test assumes Pydantic v2 is installed
        try:
            from pydantic import ConfigDict

            class Model(BaseModel):  # type: ignore
                model_config = ConfigDict(strict=True)
                value: str

            schema = pydantic_to_json_schema(Model)
            assert "properties" in schema
            assert "value" in schema["properties"]

        except ImportError:
            # Pydantic v1, skip this test
            pytest.skip("Pydantic v2 not available")


class TestSchemaValidation:
    """Test schema validation and error handling."""

    def test_convert_output_format_with_invalid_object_type(self):
        """Test error handling for invalid object types."""
        with pytest.raises(TypeError):
            convert_output_format(123)  # type: ignore

        with pytest.raises(TypeError):
            convert_output_format("not a dict")  # type: ignore

        with pytest.raises(TypeError):
            convert_output_format([1, 2, 3])  # type: ignore

    @pytest.mark.skipif(not PYDANTIC_AVAILABLE, reason="Pydantic not installed")
    def test_pydantic_to_json_schema_with_non_model(self):
        """Test error handling when passing non-Pydantic object."""
        with pytest.raises(TypeError, match="Expected a Pydantic model"):
            pydantic_to_json_schema(dict)  # type: ignore

        with pytest.raises(TypeError, match="Expected a Pydantic model"):
            pydantic_to_json_schema(str)  # type: ignore

    def test_is_pydantic_model_with_edge_cases(self):
        """Test is_pydantic_model with various edge cases."""
        assert is_pydantic_model(None) is False
        assert is_pydantic_model(123) is False
        assert is_pydantic_model("string") is False
        assert is_pydantic_model([1, 2, 3]) is False
        assert is_pydantic_model({"key": "value"}) is False
        assert is_pydantic_model(lambda x: x) is False


@pytest.mark.skipif(not PYDANTIC_AVAILABLE, reason="Pydantic not installed")
class TestComplexSchemas:
    """Test complex schema scenarios."""

    def test_deeply_nested_models(self):
        """Test deeply nested Pydantic models."""

        class Level3(BaseModel):  # type: ignore
            value: str

        class Level2(BaseModel):  # type: ignore
            level3: Level3

        class Level1(BaseModel):  # type: ignore
            level2: Level2

        schema = pydantic_to_json_schema(Level1)

        # Should have nested definitions
        assert "$defs" in schema or "definitions" in schema

    def test_list_of_models(self):
        """Test list of model objects."""

        class Item(BaseModel):  # type: ignore
            name: str
            value: int

        class Container(BaseModel):  # type: ignore
            items: list[Item]

        schema = pydantic_to_json_schema(Container)

        # items should be an array
        assert schema["properties"]["items"]["type"] == "array"

    def test_union_types(self):
        """Test union type handling."""

        class Model(BaseModel):  # type: ignore
            value: str | int

        schema = pydantic_to_json_schema(Model)

        # Should have anyOf or oneOf for union types
        value_schema = schema["properties"]["value"]
        assert "anyOf" in value_schema or "oneOf" in value_schema

    def test_model_with_default_values(self):
        """Test that default values are preserved."""

        class Model(BaseModel):  # type: ignore
            required: str
            optional_with_default: str = "default_value"
            optional_with_none: str | None = None

        schema = pydantic_to_json_schema(Model)

        # Only 'required' should be in required list
        assert "required" in schema["required"]
        assert "optional_with_default" not in schema["required"]
        assert "optional_with_none" not in schema["required"]

    def test_schema_serialization(self):
        """Test that schemas can be serialized to JSON."""

        class Model(BaseModel):  # type: ignore
            name: str
            age: int
            active: bool

        schema = pydantic_to_json_schema(Model)
        output_format = convert_output_format(Model)

        # Should be serializable to JSON
        json_str = json.dumps(schema)
        assert isinstance(json_str, str)
        assert len(json_str) > 0

        # Output format should also be serializable
        json_str2 = json.dumps(output_format)
        assert isinstance(json_str2, str)
        assert "json_schema" in json_str2
