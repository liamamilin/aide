"""Loopback service startup must not depend on the runner's DNS resolver."""

import socket
from contextlib import ExitStack, closing
from http.client import HTTPConnection

from tests.fake_ollama import FakeOllama


def test_loopback_services_start_without_reverse_dns(monkeypatch):
    def unexpected_lookup(*args, **kwargs):
        raise AssertionError("loopback test service attempted reverse DNS")

    monkeypatch.setattr(socket, "getfqdn", unexpected_lookup)
    monkeypatch.setattr(socket, "gethostbyaddr", unexpected_lookup)
    with ExitStack() as cleanup:
        for _ in range(2):
            service = FakeOllama()
            cleanup.callback(service.close)
            assert service.server.server_name == "127.0.0.1"
            assert service.server.server_port == service.server.server_address[1]
            service.enqueue({"models": []})
            with closing(HTTPConnection("127.0.0.1", service.server.server_port, timeout=2)) as client:
                client.request("GET", "/api/tags")
                response = client.getresponse()
                assert response.status == 200
                assert response.read() == b'{"models": []}\n'
            assert service.requests == [{"method": "GET", "path": "/api/tags"}]
