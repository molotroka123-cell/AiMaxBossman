# Docker service example

Adapt to the actual compose file instead of copying blindly.

```yaml
bossman-gateway:
  build: ./bossman-core
  command: python -m bossman.gateway.main
  restart: unless-stopped
  environment:
    BOSSMAN_GATEWAY_CONFIG: /app/config/gateway.yaml
    BOSSMAN_GATEWAY_CORE_KEY: ${BOSSMAN_GATEWAY_CORE_KEY}
  volumes:
    - ./config/gateway.yaml:/app/config/gateway.yaml:ro
  ports:
    - "127.0.0.1:8765:8765"
```

Binding the published port to `127.0.0.1` is intentional. Do not publish `0.0.0.0:8765` merely to make phone access easier. Remote client access belongs behind the future private networking layer.
