import json
import socket
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Tests must not access the network")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


@pytest.fixture
def strata():
    return json.loads((Path(__file__).parent / "fixtures" / "strata_monitor.json").read_text())


@pytest.fixture
def usage():
    return json.loads((Path(__file__).parent / "fixtures" / "xangi_usage.json").read_text())
