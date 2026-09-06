"""Bossman Gateway entrypoint with auto-model discovery.

Run with:
    python -m bossman.gateway.main --reload

On startup, automatically loads available models from all configured providers.
"""
import argparse
import os
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Bossman Gateway")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    parser.add_argument("--env", default=".env", help="Path to .env file")
    args = parser.parse_args()
    
    env_path = args.env
    if not os.path.isabs(env_path):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        bossman_core = os.path.join(script_dir, "..", "..")
        env_path = os.path.join(bossman_core, env_path)
    
    if os.path.exists(env_path):
        print(f"[Gateway] Loading .env from {env_path}")
        from dotenv import load_dotenv
        load_dotenv(env_path)
    else:
        print(f"[Gateway] No .env found at {env_path}, using environment variables")
    
    from .config import AVAILABLE_PROVIDERS, load_provider_config
    print("\n[Gateway] Configured providers:")
    for provider in AVAILABLE_PROVIDERS:
        config = load_provider_config(provider)
        has_key = "✓" if config.get("api_key") else "✗"
        print(f"  {has_key} {provider}")
    print()
    
    print(f"[Gateway] Starting on http://{args.host}:{args.port}")
    print(f"[Gateway] Endpoints:")
    print(f"  - GET  /health                 - Overall health")
    print(f"  - GET  /health/{{provider}}    - Provider health")
    print(f"  - GET  /v1/models              - List all models (auto-loaded)")
    print(f"  - POST /v1/chat/completions   - Unified chat")
    print(f"  - POST /gateway/refresh-models - Refresh model cache")
    print()
    
    uvicorn.run(
        "bossman.gateway.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info"
    )


if __name__ == "__main__":
    main()
