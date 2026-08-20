from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Any

from google.protobuf import descriptor_pb2

EIP_PACKAGE = "converge.agent_envd.eip.v1"
EIP_PREFIX = f".{EIP_PACKAGE}."


@dataclass(frozen=True)
class SchemaIndex:
    files: tuple[descriptor_pb2.FileDescriptorProto, ...]
    messages: dict[str, descriptor_pb2.DescriptorProto]
    enums: dict[str, descriptor_pb2.EnumDescriptorProto]
    map_entries: dict[str, descriptor_pb2.DescriptorProto]
    methods: tuple[descriptor_pb2.MethodDescriptorProto, ...]


def build_index(descriptor_set: descriptor_pb2.FileDescriptorSet) -> SchemaIndex:
    files = tuple(file for file in descriptor_set.file if file.package == EIP_PACKAGE)
    messages: dict[str, descriptor_pb2.DescriptorProto] = {}
    enums: dict[str, descriptor_pb2.EnumDescriptorProto] = {}
    map_entries: dict[str, descriptor_pb2.DescriptorProto] = {}
    methods: list[descriptor_pb2.MethodDescriptorProto] = []

    def add_message(message: descriptor_pb2.DescriptorProto, prefix: str) -> None:
        full_name = f"{prefix}.{message.name}"
        if message.options.map_entry:
            map_entries[full_name] = message
        else:
            messages[full_name] = message
        for nested in message.nested_type:
            add_message(nested, full_name)
        for enum in message.enum_type:
            enums[f"{full_name}.{enum.name}"] = enum

    for file in files:
        prefix = f".{file.package}"
        for message in file.message_type:
            add_message(message, prefix)
        for enum in file.enum_type:
            enums[f"{prefix}.{enum.name}"] = enum
        for service in file.service:
            methods.extend(service.method)

    return SchemaIndex(
        files=files,
        messages=messages,
        enums=enums,
        map_entries=map_entries,
        methods=tuple(methods),
    )


class OptionReader:
    def __init__(self, module: ModuleType) -> None:
        self._module = module

    def method(self, method: descriptor_pb2.MethodDescriptorProto) -> Any:
        if not method.options.HasExtension(self._module.eip_method):
            raise ValueError(f"method {method.name} has no eip_method option")
        return method.options.Extensions[self._module.eip_method]

    def message(self, message: descriptor_pb2.DescriptorProto) -> Any | None:
        if not message.options.HasExtension(self._module.eip_message):
            return None
        return message.options.Extensions[self._module.eip_message]

    def enum_value(self, value: descriptor_pb2.EnumValueDescriptorProto) -> Any | None:
        if not value.options.HasExtension(self._module.eip_enum_value):
            return None
        return value.options.Extensions[self._module.eip_enum_value]

    def field(self, field: descriptor_pb2.FieldDescriptorProto) -> Any | None:
        if not field.options.HasExtension(self._module.eip_field):
            return None
        return field.options.Extensions[self._module.eip_field]

    def enum_name(self, enum_type: str, value: int) -> str:
        return getattr(self._module, enum_type).Name(value)


def short_name(full_name: str) -> str:
    return full_name.rsplit(".", 1)[-1]


def real_oneofs(message: descriptor_pb2.DescriptorProto) -> dict[int, tuple[descriptor_pb2.FieldDescriptorProto, ...]]:
    grouped: dict[int, list[descriptor_pb2.FieldDescriptorProto]] = {}
    for field in message.field:
        if field.HasField("oneof_index") and not field.proto3_optional:
            grouped.setdefault(field.oneof_index, []).append(field)
    return {index: tuple(fields) for index, fields in grouped.items()}


def topological_messages(index: SchemaIndex) -> tuple[tuple[str, descriptor_pb2.DescriptorProto], ...]:
    remaining = dict(index.messages)
    ordered: list[tuple[str, descriptor_pb2.DescriptorProto]] = []
    emitted: set[str] = set()
    while remaining:
        progress = False
        for full_name in sorted(tuple(remaining)):
            message = remaining[full_name]
            dependencies = {
                field.type_name
                for field in message.field
                if field.type == descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
                and field.type_name in index.messages
                and field.type_name != full_name
            }
            if dependencies <= emitted:
                ordered.append((full_name, message))
                emitted.add(full_name)
                del remaining[full_name]
                progress = True
        if not progress:
            # Forward annotations support recursive schemas; keep deterministic order.
            for full_name in sorted(remaining):
                ordered.append((full_name, remaining[full_name]))
            break
    return tuple(ordered)
