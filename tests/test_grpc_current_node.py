from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from freeplane_tmux.grpc_client import GrpcClientError, fetch_current_node_id


def _install_fake_grpc(monkeypatch, *, response: object):
    calls: dict[str, object] = {}

    class ReadyFuture:
        def result(self, timeout: float) -> None:
            calls["ready_timeout"] = timeout

    class Channel:
        def close(self) -> None:
            calls["closed"] = True

    grpc_module = ModuleType("grpc")
    grpc_module.FutureTimeoutError = type("FutureTimeoutError", (Exception,), {})
    channel = Channel()

    def insecure_channel(address: str) -> Channel:
        calls["address"] = address
        return channel

    grpc_module.insecure_channel = insecure_channel
    grpc_module.channel_ready_future = lambda value: ReadyFuture()
    monkeypatch.setitem(sys.modules, "grpc", grpc_module)

    class Stub:
        def __init__(self, value: object) -> None:
            calls["channel"] = value

        def GetCurrentNode(self, request: object, *, timeout: float) -> object:
            calls["request"] = request
            calls["rpc_timeout"] = timeout
            return response

    pb2 = SimpleNamespace(GetCurrentNodeRequest=lambda: SimpleNamespace())
    pb2_grpc = SimpleNamespace(FreeplaneStub=Stub)
    calls["pb2"] = pb2
    calls["pb2_grpc"] = pb2_grpc
    return calls


def test_fetch_current_node_id(monkeypatch) -> None:
    calls = _install_fake_grpc(
        monkeypatch,
        response=SimpleNamespace(success=True, map_id="map-1", node_id="node-7"),
    )
    monkeypatch.setattr(
        "freeplane_tmux.grpc_client._load_stubs",
        lambda: (calls["pb2"], calls["pb2_grpc"]),
    )

    result = fetch_current_node_id(address="freeplane.example:50052", timeout=2.5)

    assert result == "node-7"
    assert calls["address"] == "freeplane.example:50052"
    assert calls["ready_timeout"] == 2.5
    assert calls["rpc_timeout"] == 2.5
    assert calls["closed"] is True


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (SimpleNamespace(success=False, node_id="node-7"), "success=false"),
        (SimpleNamespace(success=True, node_id=""), "empty node id"),
    ],
)
def test_fetch_current_node_id_rejects_invalid_response(
    monkeypatch,
    response: object,
    message: str,
) -> None:
    calls = _install_fake_grpc(monkeypatch, response=response)
    monkeypatch.setattr(
        "freeplane_tmux.grpc_client._load_stubs",
        lambda: (calls["pb2"], calls["pb2_grpc"]),
    )

    with pytest.raises(GrpcClientError, match=message):
        fetch_current_node_id(address="127.0.0.1:50051", timeout=1.0)
