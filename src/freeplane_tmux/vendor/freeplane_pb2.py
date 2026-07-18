# Generated-equivalent protobuf module derived from
# metacoma/freeplane_plugin_grpc/src/main/proto/freeplane.proto.
from __future__ import annotations

from google.protobuf import descriptor_pb2 as _descriptor_pb2
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import message_factory as _message_factory
from google.protobuf import symbol_database as _symbol_database

_sym_db = _symbol_database.Default()


fdp = _descriptor_pb2.FileDescriptorProto()
fdp.name = "freeplane.proto"
fdp.package = "freeplane"
fdp.syntax = "proto3"
fdp.options.java_multiple_files = True
fdp.options.java_package = "org.freeplane.plugin.grpc"
fdp.options.java_outer_classname = "freeplane"
fdp.options.objc_class_prefix = "FP"
fdp.options.go_package = "github.com/metacoma/freeplane_plugin_grpc/grpc/golang/freeplane;freeplane"


def _add_message(name: str, fields: list[tuple[str, int, int]]) -> None:
    msg = fdp.message_type.add()
    msg.name = name
    for field_name, field_number, field_type in fields:
        field = msg.field.add()
        field.name = field_name
        field.number = field_number
        field.label = _descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
        field.type = field_type


_add_message(
    "GroovyRequest",
    [("groovy_code", 1, _descriptor_pb2.FieldDescriptorProto.TYPE_STRING)],
)
_add_message(
    "GroovyResponse",
    [
        ("success", 1, _descriptor_pb2.FieldDescriptorProto.TYPE_BOOL),
        ("result", 2, _descriptor_pb2.FieldDescriptorProto.TYPE_STRING),
        ("error_message", 3, _descriptor_pb2.FieldDescriptorProto.TYPE_STRING),
    ],
)
_add_message("MindMapToJSONRequest", [])
_add_message(
    "MindMapToJSONResponse",
    [
        ("success", 1, _descriptor_pb2.FieldDescriptorProto.TYPE_BOOL),
        ("json", 2, _descriptor_pb2.FieldDescriptorProto.TYPE_STRING),
    ],
)

service = fdp.service.add()
service.name = "Freeplane"
for method_name, input_type, output_type in (
    ("Groovy", ".freeplane.GroovyRequest", ".freeplane.GroovyResponse"),
    (
        "MindMapToJSON",
        ".freeplane.MindMapToJSONRequest",
        ".freeplane.MindMapToJSONResponse",
    ),
):
    method = service.method.add()
    method.name = method_name
    method.input_type = input_type
    method.output_type = output_type


DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(fdp.SerializeToString())


def _message_class(name: str):
    descriptor = DESCRIPTOR.message_types_by_name[name]
    get_message_class = getattr(_message_factory, "GetMessageClass", None)
    if get_message_class is not None:
        return get_message_class(descriptor)

    factory_cls = getattr(_message_factory, "MessageFactory", None)
    if factory_cls is None:
        raise AttributeError(
            "google.protobuf.message_factory does not provide "
            "a supported message-class API"
        )

    factory = factory_cls()
    get_prototype = getattr(factory, "GetPrototype", None)
    if get_prototype is None:
        raise AttributeError(
            "google.protobuf.message_factory provides neither GetMessageClass "
            "nor MessageFactory.GetPrototype"
        )
    return get_prototype(descriptor)


GroovyRequest = _message_class("GroovyRequest")
GroovyResponse = _message_class("GroovyResponse")
MindMapToJSONRequest = _message_class("MindMapToJSONRequest")
MindMapToJSONResponse = _message_class("MindMapToJSONResponse")

_sym_db.RegisterMessage(GroovyRequest)
_sym_db.RegisterMessage(GroovyResponse)
_sym_db.RegisterMessage(MindMapToJSONRequest)
_sym_db.RegisterMessage(MindMapToJSONResponse)

__all__ = [
    "DESCRIPTOR",
    "GroovyRequest",
    "GroovyResponse",
    "MindMapToJSONRequest",
    "MindMapToJSONResponse",
]
