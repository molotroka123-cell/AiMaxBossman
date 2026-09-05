"""Pinned httpx transport shared by Core and Command Center (no second DNS lookup)."""
import httpcore
import httpx

try:
    from httpcore._backends.anyio import AnyIOBackend
except ImportError:
    AnyIOBackend = httpcore.AsyncNetworkBackend  # unsupported backend fails closed


class PinnedBackend(AnyIOBackend):
    def __init__(self, pins):
        super().__init__()
        self.pins = pins

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if isinstance(host, bytes):
            host = host.decode("ascii")
        pinned = self.pins.get(host)
        if pinned is None:
            raise ValueError("host not pinned; refusing a new DNS lookup")
        return await super().connect_tcp(pinned, port, timeout=timeout,
                                         local_address=local_address, socket_options=socket_options)


class PinnedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, pins):
        super().__init__(trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(network_backend=PinnedBackend(pins))
