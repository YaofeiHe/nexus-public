from __future__ import annotations


class SchemaValidationError(ValueError):
    pass


def validate_json(payload: dict[str, object], schema: dict[str, object]) -> dict[str, object]:
    if schema.get("type") != "object":
        raise SchemaValidationError("Only object schemas are supported by the MVP validator")
    required = schema.get("required", [])
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in payload:
                raise SchemaValidationError(f"Missing required field: {key}")
    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        for key, spec in properties.items():
            if key in payload and isinstance(spec, dict):
                _validate_value(key, payload[key], spec)
    return payload


def _validate_value(path: str, value: object, spec: dict[str, object]) -> None:
    expected = spec.get("type")
    if expected == "string" and not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be string")
    if expected == "boolean" and not isinstance(value, bool):
        raise SchemaValidationError(f"{path} must be boolean")
    if expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        raise SchemaValidationError(f"{path} must be number")
    if expected == "array":
        if not isinstance(value, list):
            raise SchemaValidationError(f"{path} must be array")
        item_spec = spec.get("items")
        if isinstance(item_spec, dict):
            for index, item in enumerate(value):
                _validate_value(f"{path}[{index}]", item, item_spec)
    if expected == "object":
        if not isinstance(value, dict):
            raise SchemaValidationError(f"{path} must be object")
        required = spec.get("required", [])
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    raise SchemaValidationError(f"{path}.{key} is required")
        props = spec.get("properties", {})
        if isinstance(props, dict):
            for key, child_spec in props.items():
                if key in value and isinstance(child_spec, dict):
                    _validate_value(f"{path}.{key}", value[key], child_spec)
