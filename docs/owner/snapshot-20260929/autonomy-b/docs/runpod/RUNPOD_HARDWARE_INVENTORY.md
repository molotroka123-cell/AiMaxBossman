# RUNPOD HARDWARE INVENTORY — TEMPLATE (заполняется на реальном поде)

**Статус:** этот файл — ШАБЛОН. Он не заявляет цифр, которые не были измерены
на реальном RunPod-инстансе. Заполнить одной командой при первом входе на под:

```bash
git fetch --all --prune
python tools/runpod_preflight.py --json > docs/runpod/runpod_preflight_raw.json
python tools/runpod_preflight.py                 # человекочитаемый вывод ниже
nvidia-smi --query-gpu=name,memory.total,driver_version,pstate --format=csv
df -h /workspace 2>/dev/null || echo "нет /workspace"
```

Значения ниже — placeholder `<НЕ ИЗМЕРЕНО>`, не выдуманные числа.

## Идентификация

| Поле | Значение |
|---|---|
| RunPod pod ID | `<НЕ ИЗМЕРЕНО>` |
| Дата/время инвентаризации (UTC) | `<НЕ ИЗМЕРЕНО>` |
| START_REMOTE_SHA | `<заполнить git rev-parse HEAD на старте>` |

## Compute

| Поле | Значение | Источник |
|---|---|---|
| GPU model | `<НЕ ИЗМЕРЕНО>` | `nvidia-smi --query-gpu=name` |
| GPU count | `<НЕ ИЗМЕРЕНО>` | `nvidia-smi -L \| wc -l` |
| VRAM total (на GPU) | `<НЕ ИЗМЕРЕНО>` | `nvidia-smi --query-gpu=memory.total` |
| VRAM free (idle, до загрузки моделей) | `<НЕ ИЗМЕРЕНО>` | `nvidia-smi --query-gpu=memory.free` |
| Driver version | `<НЕ ИЗМЕРЕНО>` | `nvidia-smi --query-gpu=driver_version` |
| CUDA (nvcc) | `<НЕ ИЗМЕРЕНО>` | `nvcc --version` |
| CUDA (torch, если установлен) | `<НЕ ИЗМЕРЕНО>` | `python -c "import torch;print(torch.version.cuda)"` |
| CPU | `<НЕ ИЗМЕРЕНО>` | `lscpu \| grep 'Model name'` |
| CPU cores | `<НЕ ИЗМЕРЕНО>` | `nproc` |
| RAM total | `<НЕ ИЗМЕРЕНО>` | `free -h` |
| Disk (root) | `<НЕ ИЗМЕРЕНО>` | `df -h /` |
| Disk (/workspace, если есть) | `<НЕ ИЗМЕРЕНО>` | `df -h /workspace` |
| Persistent workspace | `<НЕ ИЗМЕРЕНО: YES/NO>` | `python tools/runpod_preflight.py` |

## Software

| Поле | Значение |
|---|---|
| OS / distro | `<НЕ ИЗМЕРЕНО>` |
| Python version | `<НЕ ИЗМЕРЕНО>` |
| Docker | `<НЕ ИЗМЕРЕНО: available/version>` |
| Ollama binary | `<НЕ ИЗМЕРЕНО: YES/NO>` |
| Ollama reachable (OLLAMA_HOST) | `<НЕ ИЗМЕРЕНО>` |
| HuggingFace cache dir | `<НЕ ИЗМЕРЕНО>` |
| PostgreSQL reachable | `<НЕ ИЗМЕРЕНО>` |

## Network

| Поле | Значение |
|---|---|
| Outbound network | `<НЕ ИЗМЕРЕНО>` |
| RunPod public IP / proxy | `<НЕ ИЗМЕРЕНО>` |

## Стоимость

| Поле | Значение |
|---|---|
| RunPod hourly price (USD) | `<НЕ ИЗМЕРЕНО — заполнить из RunPod dashboard при аренде>` |
| GPU tier выбран | `<НЕ ИЗМЕРЕНО>` |

## Честная граница

Этот документ **не заявляет** ни одного GPU/VRAM числа, пока преамбула выше
не будет выполнена на реальном RunPod-поде. `tools/runpod_preflight.py`
специально спроектирован для заполнения этой таблицы одним прогоном —
никаких ручных догадок.
