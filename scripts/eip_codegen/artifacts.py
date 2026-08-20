from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from .model import OptionReader, SchemaIndex, short_name
from .python_renderer import method_records


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_models(models_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("converge_generated_eip_models", models_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load generated EIP models")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_schema(index: SchemaIndex, models_path: Path) -> dict[str, object]:
    module = _load_models(models_path)
    definitions: dict[str, object] = {}
    wire_names = sorted(
        {short_name(name) for name in index.messages if not short_name(name).startswith("EIPMethodOptions")}
        | {short_name(name) for name in index.enums}
    )
    for name in wire_names:
        value = getattr(module, name, None)
        if value is None:
            continue
        schema = TypeAdapter(value).json_schema(ref_template="#/$defs/{model}")
        nested = schema.pop("$defs", {})
        for nested_name, nested_schema in nested.items():
            existing = definitions.get(nested_name)
            if existing is not None and existing != nested_schema:
                raise ValueError(f"conflicting JSON Schema definition for {nested_name}")
            definitions[nested_name] = nested_schema
        if schema != {"$ref": f"#/$defs/{name}"}:
            existing = definitions.get(name)
            if existing is None:
                definitions[name] = schema
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/converge-ai-labs/agent-foundation/eip/v1/schema.json",
        "title": "Environment Interaction Protocol 1.0",
        "$defs": definitions,
    }


def build_openrpc(records: list[dict[str, Any]], schema: dict[str, object]) -> dict[str, object]:
    definitions = schema["$defs"]
    if not isinstance(definitions, dict):
        raise ValueError("EIP JSON Schema definitions must be an object")
    methods: list[dict[str, object]] = []
    for record in records:
        params_type = record["params_type"]
        definition = definitions[params_type]
        if not isinstance(definition, dict) or not isinstance(definition.get("properties"), dict):
            raise ValueError(f"EIP params schema {params_type} must be an object")
        properties = definition["properties"]
        required = set(definition.get("required", ()))
        params = [
            {
                "name": name,
                "required": name in required,
                "schema": {"$ref": f"schema.json#/$defs/{params_type}/properties/{name}"},
            }
            for name in properties
        ]
        method: dict[str, object] = {
            "name": record["jsonrpc_method"],
            "params": params,
            "paramStructure": "by-name",
            "x-eip-capability": record["capability"],
            "x-eip-kind": record["kind"],
            "x-eip-idempotency": record["idempotency"],
            "x-eip-idempotency-key": record["idempotency_key"],
            "x-eip-introduced": record["introduced"],
            "x-eip-error-family": record["error_family"],
        }
        if record["result_type"] is not None:
            method["result"] = {
                "name": "result",
                "schema": {"$ref": f"schema.json#/$defs/{record['result_type']}"},
            }
        methods.append(method)
    return {
        "openrpc": "1.3.2",
        "info": {"title": "Environment Interaction Protocol", "version": "1.0"},
        "methods": methods,
    }


def write_artifacts(
    output_dir: Path,
    index: SchemaIndex,
    options: OptionReader,
    descriptor_sha256: str,
    models_path: Path,
) -> list[Path]:
    records = method_records(index, options)
    inventory = {
        "generated": True,
        "protocol": {"package": "converge.agent_envd.eip.v1", "version": "1.0"},
        "descriptor_sha256": descriptor_sha256,
        "request_method_count": sum(record["kind"] == "request_response" for record in records),
        "notification_count": sum(record["kind"] == "server_notification" for record in records),
        "methods": records,
    }
    schema = build_schema(index, models_path)
    outputs = {
        "methods.json": inventory,
        "schema.json": schema,
        "openrpc.json": build_openrpc(records, schema),
    }
    paths: list[Path] = []
    for name, value in outputs.items():
        path = output_dir / name
        _write_json(path, value)
        paths.append(path)
    return paths
