# Bossman Motion Concert: концертное видео на весь трек одной командой

## Команда на завтра

```powershell
cd C:\Users\asd\Bossman\Bossman_Motion_Concert_Faint
C:\Users\asd\Bossman\media-runtime\comfyui-venv\Scripts\python.exe pipeline\concert.py all
```

`all` выполняет `plan`, `run`, `assemble` и `qc` по очереди. Повторный запуск той же команды продолжает с места
остановки: готовые шоты не генерируются заново.

Сначала можно посмотреть план и команды без GPU:

```powershell
C:\Users\asd\Bossman\media-runtime\comfyui-venv\Scripts\python.exe pipeline\concert.py plan
C:\Users\asd\Bossman\media-runtime\comfyui-venv\Scripts\python.exe pipeline\concert.py run --dry-run --limit 3
```

## Что делает каждая подкоманда

| Подкоманда | Что делает |
|---|---|
| `plan` | Строит `work\manifest.json` из `C:\Users\asd\Bossman\motion-concert\work\analysis.json`. Шоты покрывают всю шкалу 0…168.829388 с без дыр и без перекрытий. Секции нарезаются на блоки примерно по 4.8 с (каждый не длиннее 4.8125 с), склейки привязаны к сетке битов 136 BPM. Около 70% секунд занимают шоты певицы (S2V), остальное отдаётся вставкам I2V по кругу band_wide → shamisen_hands → crowd → stage_led. Для каждого шота записываются id, take, start_s/end_s, kind, путь и sha256 референса, prompt, seed, params и status. |
| `run` | Очередь, в которой идёт одна тяжёлая задача за раз. Сначала генерируются все S2V-шоты в одной сессии ComfyUI (порт 8189, Wan2.2 S2V-14B fp8, 77 кадров при 16 fps, 832x480, 20 шагов). Затем ComfyUI останавливается и запускаются вставки через `sd-cli -M vid_gen` (Wan2.2-TI2V-5B Q8, 24 fps, 20 шагов, без `--diffusion-fa` и `--vae-tiling`). Если GPU занят чужим `sd-cli.exe` или чем-то на :8189, скрипт ждёт и не конкурирует. Manifest записывается после каждого шота. |
| `assemble` | Обрезает каждый шот точно по его слоту на сетке 24 fps и выводит в 854x480 с полями по бокам (pad), без растяжения. Склеивает шоты в 4052 кадра, затем добавляет ОРИГИНАЛЬНЫЙ mp3 через `-c:a copy`: трек не перекодируется и не обрезается, `-shortest` не используется. |
| `qc` | Через ffprobe проверяет потоки, длительность относительно 168.829388 (допуск 1 кадр), число кадров и A/V offset. Делает полный decode (`ffmpeg -v error -i X -f null -`) и сравнивает md5 аудиопакетов с исходником, то есть проверяет, что звук сохранён бит в бит. Результат пишется в `out\qc.json`. |

Опции: `--limit N` ограничивает число шотов за запуск, `--dry-run` только печатает команды (GPU не используется, статусы не
меняются), `--shot-timeout` задаёт лимит секунд на один шот (по умолчанию 5400).

## STOP и продолжение

- **Остановить:** создайте пустой файл `C:\Users\asd\Bossman\Bossman_Motion_Concert_Faint\pipeline\STOP`. Скрипт проверяет его
  между шотами и во время ожидания GPU. Текущий шот доработает, после этого скрипт выйдет с кодом 3. Ctrl+C прерывает
  текущий шот: для ComfyUI отправляется `/interrupt`, процесс sd-cli завершается. Шот получает статус
  `interrupted`, manifest сохраняется.
- **Продолжить:** удалите `STOP` и запустите ту же команду. Шот пропускается, если его выход существует и проходит
  ffprobe. Упавшие шоты запускаются заново со следующим `take`.
- **Пере-план:** `plan` можно запускать повторно. Шоты с тем же слотом, prompt, seed и референсом сохраняют свои результаты.

## Где что лежит

- `pipeline\work\manifest.json` содержит план и историю запусков. Для каждой попытки записаны wall_s, модель и файлы, steps, seed, device, peak_rss_gb, status и reject_reason.
- `pipeline\work\shots\mc_<id>_t<take>.mp4` — готовые шоты без звука.
- `pipeline\work\logs\` — логи ComfyUI и sd-cli, `*_workflow_api.json` и prompt-файлы.
- `pipeline\work\refs\` — референсы, приведённые к 832x480 (cover + crop по центру).
- `pipeline\out\faint_concert_854x480.mp4` — итоговое видео.
- `pipeline\out\qc.json` — вердикт `PASS`, `FAIL_GAPS` или `FAIL`, список дыр и проверки.
- Временные входы S2V: `ComfyUI\input\mc_*.wav` (нарезка звука для аудио-энкодера) и `ComfyUI\output\mc_*_*.png` (кадры).

## Дыры

Если шот не удался, `assemble` ставит на его место тёмно-красную заглушку той же длины. Шкала не съезжает, а дыра
видна глазом. Этот шот попадает в `qc.json → gaps` с причиной, и вердикт становится `FAIL_GAPS`. Соседний клип в дыру
никогда не зацикливается и не растягивается.

## Предусловия

- ffmpeg и ffprobe доступны в PATH (WinGet).
- ComfyUI `C:\Users\asd\Bossman\media-runtime\ComfyUI` с моделями `wan2.2_s2v_14B_fp8_scaled`, `umt5_xxl_fp8_e4m3fn_scaled`,
  `wan_2.1_vae`, `wav2vec2_large_english_fp16`. Порт 8189 должен быть свободен.
- `C:\Users\asd\Bossman\media-runtime\sdcpp\vulkan\sd-cli.exe` и файлы в `C:\Users\asd\Bossman\models\media\wan22-ti2v-5b`.
- GPU свободен: нет чужих `sd-cli.exe`, ComfyUI, ollama или llama-server с большими моделями. Скрипт сам ждёт только
  `sd-cli.exe` и занятый :8189. Остальные процессы нужно остановить вручную.
- Только stdlib Python, librosa и sklearn не нужны. `analysis.json` уже готов.

## Чего НЕ проверено (честно)

- В этом пайплайне ни один кадр ещё не сгенерирован. Проверены только `plan`, `run --dry-run`, STOP, resume и
  `assemble`+`qc` на синтетических клипах testsrc на CPU.
- Время на полный прогон неизвестно. 31 S2V и 14 I2V шотов, по одному за раз, займут вероятно много часов, и это не измерено.
- Качество lip-sync, точность рук и сямисэна (инструменты), одинаковость лица между шотами, артефакты и цветовой шум
  автоматически не проверяются. Это смотрит человек.
- Где именно поёт голос, не определено (`not_claimed` в analysis.json). S2V назначается по энергии секции, это прокси.
- В `analysis.json` нет времён битов, только их количество. Склейки привязаны к сетке из `tempo_bpm=136` и
  `first_beat_s=0.093`, а не к реальным битам.
- Peak RSS для S2V — это пик процесса ComfyUI с момента его старта (накопительный), а не пик отдельного шота.
