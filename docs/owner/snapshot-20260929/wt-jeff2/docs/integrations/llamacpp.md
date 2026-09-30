# llama.cpp in Bossman

Bossman reuses its existing `openai_compat` provider for llama-server. There is
no second inference gateway: model calls retain the existing credentials,
privacy checks, budgets and tool execution permissions.

## Windows setup

1. Obtain a pinned official release from
   <https://github.com/ggml-org/llama.cpp/releases>. Record the release version
   and archive SHA-256. For Radeon, select a build supporting Vulkan; this
   document does not certify GPU compatibility or performance on owner hardware.
2. Place a compatible GGUF model on disk and check its source and licence.
   A downloaded model is not automatically loaded or tested by Bossman.
3. Start the installed executable in PowerShell, adjusting the paths:

   ```powershell
   & 'C:\Bossman\llama\llama-server.exe' -m 'D:\Models\model.gguf' --host 127.0.0.1 --port 8080 --alias bossman-local --ctx-size 8192 --jinja
   ```

   This example binds to this computer only. Use the release's `--help` and
   `--list-devices` to check GPU support before configuring offload. The example
   does not download weights or assume that all 128 GB are available to one model.
4. In Bossman's provider settings choose `openai_compat` and base URL
   `http://127.0.0.1:8080/v1`; select the actual model ID returned by discovery
   (`bossman-local` with the command above). If the server uses an API key,
   save that key in the existing provider credential field. Anonymous discovery
   may report 401 for a protected server; use its authenticated provider check.
5. Optionally set `BCC_MODELS_DIRS=D:\Models` before starting Bossman to include
   that directory in the existing GGUF file scan. Multiple Windows directories
   use a semicolon separator.

## What the integration checks

Discovery and provider health validate the `/v1/models` response. An HTML page,
malformed catalog, empty catalog, loading-only catalog, or failed model load
cannot produce a successful health result. Model IDs remain unchanged.

Discovery also returns selected `model_details`: owner, reported training
context, parameter count, model size and loading state when supplied by the
server. Training context is not a measured usable context limit. Router launch
arguments and arbitrary server fields are not returned, because those may
contain credentials.

Unloaded or sleeping router models remain discoverable: llama-server can load
them on demand. Discovery never loads models, generates tokens or tests quality.
An available catalog therefore does not prove successful inference.

## Owner acceptance

After server readiness, run a Russian chat, an allowed file-read tool task, a
denied file-write task and a JSON-schema task through Bossman. Confirm actual
output, permission handling, latency and resource usage. Tool calling depends
on the chosen model and chat template; `--jinja` alone does not establish it.
These checks and real Radeon acceleration still require the owner's runtime.

API source: <https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>.
Focused contract tests: `command-center/tests/test_llamacpp_catalog.py`.
