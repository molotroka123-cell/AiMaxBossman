import pytest
from bossman.gateway.config import AliasConfig, BackendConfig, GatewayConfig, ModelTarget
from bossman.gateway.backends import OpenAIBackend
from bossman.gateway.router import ModelRouter, RouteNotFound
import httpx


def test_priority_and_capabilities():
    c=GatewayConfig(
        backends={"a":BackendConfig("a","http://a"),"b":BackendConfig("b","http://b")},
        aliases={"x":AliasConfig("x",[
            ModelTarget("a","text",20,{"text"}),
            ModelTarget("b","vision",10,{"text","vision"}),
        ])}
    )
    t=httpx.MockTransport(lambda r:httpx.Response(200,json={}))
    r=ModelRouter(c,{"a":OpenAIBackend(c.backends["a"],t),"b":OpenAIBackend(c.backends["b"],t)})
    assert r.resolve("x")[0].backend_name=="b"
    assert r.resolve("x",{"vision"})[0].backend_name=="b"
    with pytest.raises(RouteNotFound): r.resolve("x",{"embeddings"})
