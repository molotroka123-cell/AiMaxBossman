"""Bossman Gateway CLI with multi-provider model listing.

Usage:
    bossman models list --provider openrouter
    bossman models list --provider ollama
    bossman models list --all
"""
import asyncio
import click
import json
import os
from typing import Optional

from .gateway.config import load_provider_config, AVAILABLE_PROVIDERS
from .gateway.backends import get_backend


@click.group()
def models():
    """Manage LLM models."""
    pass


@models.command("list")
@click.option("--provider", "-p", type=click.Choice(AVAILABLE_PROVIDERS), help="Provider name")
@click.option("--all", "-a", "list_all", is_flag=True, help="List models from all configured providers")
@click.option("--json", "-j", "output_json", is_flag=True, help="Output as JSON")
def list_models(provider: Optional[str], list_all: bool, output_json: bool):
    """List available models from provider(s)."""
    
    async def fetch_models(prov: str):
        config = load_provider_config(prov)
        if not config.get("api_key"):
            return {"provider": prov, "status": "error", "error": "No API key configured"}
        
        try:
            backend = get_backend(prov, config["api_key"], config.get("base_url"))
            models_list = await backend.list_models()
            return {
                "provider": prov,
                "status": "ok",
                "models_count": len(models_list),
                "models": models_list[:50]
            }
        except Exception as e:
            return {"provider": prov, "status": "error", "error": str(e)}
    
    async def run():
        if list_all:
            tasks = []
            for prov in AVAILABLE_PROVIDERS:
                config = load_provider_config(prov)
                if config.get("api_key"):
                    tasks.append(fetch_models(prov))
            
            if not tasks:
                click.echo("No providers configured with API keys.")
                click.echo("\nSet API keys in bossman-core/.env:")
                for prov in AVAILABLE_PROVIDERS:
                    env_var = f"{prov.upper()}_API_KEY"
                    click.echo(f"  {env_var}=...")
                return
            
            results = await asyncio.gather(*tasks)
        elif provider:
            results = [await fetch_models(provider)]
        else:
            click.echo("Specify --provider or --all")
            return
        
        if output_json:
            click.echo(json.dumps(results, indent=2))
        else:
            for result in results:
                if result["status"] == "ok":
                    click.echo(f"\n{result['provider'].upper()}: {result['models_count']} models")
                    for model in result["models"][:10]:
                        click.echo(f"  - {model}")
                    if result["models_count"] > 10:
                        click.echo(f"  ... and {result['models_count'] - 10} more")
                else:
                    click.echo(f"\n{result['provider'].upper()}: ERROR - {result.get('error', 'Unknown')}")
    
    asyncio.run(run())


if __name__ == "__main__":
    models()
