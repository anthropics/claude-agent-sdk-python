"""Utilities for converting Pydantic models to JSON schemas for structured outputs."""

import json
from copy import deepcopy
from typing import Any

try:
    from pydantic import BaseModel
    from pydantic.version import VERSION as PYDANTIC_VERSION

    PYDANTIC_AVAILABLE = True
    PYDANTIC_V2 = PYDANTIC_VERSION.startswith("2.")
except ImportError:
    PYDANTIC_AVAILABLE = False
    PYDANTIC_V2 = False
    BaseModel = None  # type: ignore


def is_pydantic_model(obj: Any) -> bool:
    """Check if an object is a Pydantic model class."""
    if not PYDANTIC_AVAILABLE:
        return False
    try:
        return isinstance(obj, type) and issubclass(obj, BaseModel)
    except TypeError:
        return False


def pydantic_to_json_schema(model: type[Any]) -> dict[str, Any]:
    """Convert a Pydantic model to a JSON schema compatible with Anthropic's structured outputs.

    Args:
        model: A Pydantic model class (must be a subclass of pydantic.BaseModel)

    Returns:
        A dictionary representing the JSON schema

    Raises:
        ImportError: If pydantic is not installed
        TypeError: If the provided object is not a Pydantic model
        ValueError: If the schema cannot be generated
    """
    if not PYDANTIC_AVAILABLE:
        raise ImportError(
            "Pydantic is not installed. Install it with: pip install pydantic"
        )

    if not is_pydantic_model(model):
        raise TypeError(f"Expected a Pydantic model class, got {type(model).__name__}")

    try:
        # Pydantic v2 uses model_json_schema(), v1 uses schema()
        schema = model.model_json_schema() if PYDANTIC_V2 else model.schema()

        # Validate and clean the schema for Anthropic API
        cleaned_schema = _clean_schema_for_anthropic(schema)
        return cleaned_schema

    except Exception as e:
        raise ValueError(
            f"Failed to generate JSON schema from Pydantic model: {e}"
        ) from e


def _clean_schema_for_anthropic(schema: dict[str, Any]) -> dict[str, Any]:
    """Clean and validate a JSON schema for use with Anthropic's structured outputs.

    Removes Pydantic-specific fields that might not be compatible with Anthropic API.

    Args:
        schema: The raw JSON schema from Pydantic

    Returns:
        A cleaned schema compatible with Anthropic's API
    """
    # Create a deep copy to avoid modifying the original
    cleaned = deepcopy(schema)

    # Remove $schema if present (Anthropic doesn't need it)
    cleaned.pop("$schema", None)

    # Remove definitions/defs if they're not used (keep if referenced)
    # Pydantic v2 uses "$defs", v1 uses "definitions"
    if "$defs" in cleaned and not _schema_uses_refs(cleaned, "$defs"):
        cleaned.pop("$defs", None)
    if "definitions" in cleaned and not _schema_uses_refs(cleaned, "definitions"):
        cleaned.pop("definitions", None)

    return cleaned


def _schema_uses_refs(schema: dict[str, Any], defs_key: str) -> bool:
    """Check if a schema uses $ref to reference definitions.

    Args:
        schema: The JSON schema
        defs_key: The key for definitions ("$defs" or "definitions")

    Returns:
        True if the schema contains $ref, False otherwise
    """
    schema_str = json.dumps(schema)
    return "$ref" in schema_str


def convert_output_format(
    output_format: dict[str, Any] | type | None,
) -> dict[str, Any] | None:
    """Convert an output_format parameter to the format expected by Anthropic API.

    Handles both raw JSON schemas and Pydantic models.

    Args:
        output_format: Either a dict containing a JSON schema or a Pydantic model class

    Returns:
        A dictionary in the format: {"type": "json_schema", "schema": {...}}
        or None if output_format is None

    Raises:
        TypeError: If output_format is not a dict or Pydantic model
        ValueError: If the schema is invalid
    """
    if output_format is None:
        return None

    # If it's already a dict, validate it has the right structure
    if isinstance(output_format, dict):
        # Check if it's already in the full format with "type" and "schema"
        if "type" in output_format and "schema" in output_format:
            if output_format["type"] != "json_schema":
                raise ValueError(
                    f"Invalid output_format type: {output_format['type']}. "
                    "Only 'json_schema' is supported."
                )
            return output_format

        # Otherwise, assume it's a raw schema and wrap it
        return {"type": "json_schema", "schema": output_format}

    # If it's a Pydantic model, convert it
    if is_pydantic_model(output_format):
        schema = pydantic_to_json_schema(output_format)
        return {"type": "json_schema", "schema": schema}

    raise TypeError(
        f"output_format must be a dict (JSON schema) or a Pydantic model, "
        f"got {type(output_format).__name__}"
    )
