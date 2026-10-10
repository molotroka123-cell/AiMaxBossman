# RunPod: обучение SDXL LoRA (kohya sd-scripts) - рабочий рецепт 10.10.2026

Инструмент: `tools/runpod/lora_train.py` (deploy / setup / upload / train / status / fetch / terminate),
оценка: `tools/runpod/lora_eval.py` (sd-cli Vulkan + arcface FaceFusion).
Прогон 10.10: три LoRA подряд на одном поде (gfgirl, p4ela, kisliy), A100 80GB PCIe SECURE, $1.79/ч, 67 минут, ~$2.0.

## Правила безопасности (обязательно)

1. Ключ RunPod лежит только в локальном env-файле (`RUNPOD_API_KEY=`), в git и в логи не попадает, в вывод не печатается.
2. Приватные фото уходят только на приватный под, по явному согласию владельца; после скачивания и проверки sha256
   `rm -rf /workspace/{dataset,output,models}` и `podTerminate`.
3. Сторожевой процесс (`deploy --watchdog-min N`) завершает под по таймеру в любом случае. Владелец сказал STOP - terminate сразу.
4. В конце ОБЯЗАТЕЛЬНО проверить через API: `pods: []`, `currentSpendPerHr: 0`, `networkVolumes: []` (команда `terminate` это делает).
5. Фото, датасеты и .safetensors в git не кладём.

## Порядок

```
python tools/runpod/lora_train.py deploy   --env <KEY.env> --ssh-key <id_ed25519> --watchdog-min 180
python tools/runpod/lora_train.py setup                      # ~10 минут, фоном на поде
python tools/runpod/lora_train.py upload   --model <base.safetensors> --dataset <upload_dir>
python tools/runpod/lora_train.py train    --job gfgirl:1500:2:1e-4:500 --job p4ela:1500:2:1e-4:500 --job kisliy:1200:1:6e-5:300
python tools/runpod/lora_train.py status
python tools/runpod/lora_train.py fetch    --out <lora-output>   # sha256 с пода и локально
python tools/runpod/lora_train.py terminate
```

Датасет: `<upload_dir>/<job>/<repeats>_<name>/*.jpg + *.txt`. Подпись начинается с триггерного слова, дальше только видимое
(выражение, поза, причёска, одежда, фон, ракурс); слов про личность нет, чтобы триггер нёс только идентичность.

## Параметры, которые отработали

| Параметр | Значение |
|---|---|
| База | epicrealismXL_pureFix.safetensors (6.9 GB, загружена с ПК, sha256 совпал) |
| Режим | `sdxl_train_network.py`, bf16, `--sdpa`, unet-only, dim16 alpha16, AdamW8bit, cosine, warmup 50 |
| Разрешение | 1024, bucket 512-1536 step 64, `--cache_latents --cache_latents_to_disk --cache_text_encoder_outputs` |
| gfgirl | 57 изображений, 1500 шагов, batch 2, lr 1e-4, 12:13, 2.04 it/s, loss 0.114 |
| p4ela (pchela v2) | 71 изображение (40 старых + 31 новый кадр), 1500 шагов, batch 2, lr 1e-4, 11:39, 2.14 it/s, loss 0.117 |
| kisliy | 7 изображений, 1200 шагов, batch 1, lr 6e-5, 7:51, 2.55 it/s, loss 0.056 |
| Чекпоинты | `--save_every_n_steps` + финальный; выбирать по arcface/глазами, не брать последний вслепую |

Без `--gradient_checkpointing` на A100 80GB влезает batch 2 при 1024.

## Грабли (все встречены)

- Образ `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`; `dockerArgs` не задавать (переопределение старта убило sshd).
- SSH-ключ должен быть в настройках АККАУНТА (`updateUserSettings(input:{pubKey})`, дописать к существующим, сначала бэкап)
  ДО деплоя; переменная `PUBLIC_KEY` на поде не работает. Порт/ip - из `pod{runtime{ports}}` (опрос).
- Сеть: перед установкой замерить скорость (на community бывает 145 KB/s). Критерий 10 MB/s; плохой хост - terminate и новый.
- HEAD sd-scripts требует transformers 5.x / diffusers 0.40, им нужен torch >= 2.5, а в образе 2.4.x: падает импорт
  ("PyTorch >= 2.5 is required"). Лечение: `pip install torch==2.6.0 torchvision==0.21.0 --index-url .../whl/cu124`.
- После этого старый torchaudio ломает импорт (`undefined symbol ... fft_irfft`): `pip uninstall -y torchaudio`.
- Проверять импорт ДО запуска очереди: `python -c "import library.train_util, library.sdxl_train_util"`. Очередь из трёх
  заданий без проверки сгорает за 30 секунд (три rc=1).
- `--cache_text_encoder_outputs` несовместим с `--shuffle_caption` / caption dropout (assert). Либо кэш, либо shuffle.
- kohya требует подпапку `<repeats>_<name>`; `--network_module=networks.lora` указывать явно.
- `huggingface-cli` удалён, использовать `hf download`.
- Долгие шаги запускать только отдельным скриптом через `nohup setsid ... &` и читать лог; `pkill -f <имя>` по ssh убивает
  и собственную ssh-сессию (имя есть в командной строке).
- Win-хост: `nohup ... &` из shell-инструмента умирает вместе с вызовом; фоновые подписи гнать через run_in_background.

## Подготовка датасета (что делали локально)

- Лица/эмбеддинги: FaceFusion `get_many_faces` (arcface), эталон - среднее по студийным фото; порог согласованности ~0.55,
  размер лица >= 150 px, резкость, дедупликация соседних кадров по признакам лица (кластеризация, порог 0.88).
- Кадры из круглых видео: белые углы круга убраны inpaint + кроп вокруг лица, без чёрных полос.
- Фото с несколькими людьми: кроп только на нужного человека.
- Подписи: локальная VL-модель через Ollama (`bossman-fast-qwen36-vision`, think=false), ~2-8 секунд на кадр; второй облачный
  сервис не нужен. Для персонажа с кепкой подпись обязана честно говорить "with cap"/"bare head" (проставлено вручную).

## Оценка

`lora_eval.py`: на каждый промпт и масштаб (0 = контроль без LoRA, 0.7, 0.8, 1.0) рендерит sd-cli и считает косинус
arcface с усреднённым эталоном. ~1-1.5 минуты на картинку 704x896, 20 шагов, Vulkan (iGPU), поэтому 6 промптов x 3 масштаба = ~20-25 минут на LoRA.
Финальный файл `*-lora.safetensors` побайтно по тензорам совпал с последним чекпоинтом.

Результаты прогона 10.10 (среднее arcface, 6 тестовых промптов, чекпоинт 1500; у kisliy 1200):

| LoRA | без LoRA | 0.7 | 0.8 | 1.0 | вывод |
|---|---|---|---|---|---|
| gfgirl | 0.02 | 0.24 | 0.28 | 0.36 | слабая похожесть по arcface; причёска/цвет волос управляются промптом, лёгкий селфи-ракурс |
| p4ela (pchela v2) | 0.06 | 0.48 | 0.50 | 0.48 | лучший масштаб 0.8; на 1.0 пересвет/шапка-артефакты |
| kisliy | 0.10 | 0.34 (3 шт.) | 0.35 | 0.43 | кепка/без кепки слушается; сильная утечка фона салона и халата (7 картинок) |

Выводы: arcface по сгенерированным картинкам ниже, чем по реальным кадрам (малые лица, широкоугольный селфи-стиль);
сравнивать надо между масштабами и с контролем без LoRA, а не с абсолютным порогом. Утечку селфи-кадрирования смотреть
глазами по монтажу. Для малых датасетов (7 фото) 1200 шагов уже дают переобучение (loss 0.056): брать чекпоинт 600-900 или
больше разнообразия фонов.
