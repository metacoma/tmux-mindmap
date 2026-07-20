# Generated-equivalent gRPC module derived from
# metacoma/freeplane_plugin_grpc/grpc/python/freeplane_pb2_grpc.py.
from __future__ import annotations

import grpc

from . import freeplane_pb2 as freeplane__pb2


class FreeplaneStub:
    """Client stub for the subset of Freeplane RPCs used by freeplane-tmux."""

    def __init__(self, channel: grpc.Channel) -> None:
        self.Groovy = channel.unary_unary(
            "/freeplane.Freeplane/Groovy",
            request_serializer=freeplane__pb2.GroovyRequest.SerializeToString,
            response_deserializer=freeplane__pb2.GroovyResponse.FromString,
        )
        self.MindMapToJSON = channel.unary_unary(
            "/freeplane.Freeplane/MindMapToJSON",
            request_serializer=freeplane__pb2.MindMapToJSONRequest.SerializeToString,
            response_deserializer=freeplane__pb2.MindMapToJSONResponse.FromString,
        )
        self.GetCurrentNode = channel.unary_unary(
            "/freeplane.Freeplane/GetCurrentNode",
            request_serializer=freeplane__pb2.GetCurrentNodeRequest.SerializeToString,
            response_deserializer=freeplane__pb2.GetCurrentNodeResponse.FromString,
        )


__all__ = ["FreeplaneStub"]
