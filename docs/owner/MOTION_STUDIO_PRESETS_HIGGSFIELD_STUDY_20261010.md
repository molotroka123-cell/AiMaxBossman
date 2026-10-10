# Motion Studio: пресеты «как у Higgsfield Genjutsu» — разбор и план на завтра (10.10.2026)

Источник: публичный каталог Genjutsu «Trending» (12 пресетов, только просмотр; генерации и кредиты не тратились;
баланс Higgsfield 0). Превью (600 px) скачаны локально для анализа структуры, не для публикации:
`video-testset-private/higgsfield-previews/` + `analysis.json` (`tools/trend_edit/analyze_edit.py`).

## Что такое пресет у них
Пресет = ведущее видео (driving video) + референс персонажа пользователя. Операция по запросу:
`motion_control` (копировать движение/камеру) или `replace_object` (заменить субъект/объект).

## Два типа (по структуре превью)
| Тип | Пресеты | Формат | Монтаж | BPM |
|---|---|---|---|---|
| A. «один дубль» — превращение в кадре | Arcade Identity Swap, Ceiling Fan Transition, Showroom Pickup, Rug Transition Swap, Nightclub Balcony Observer | 9:16, 13–15 с | 0–2 склейки | 92–144 |
| B. «шоурил» — быстрая нарезка | Supercar Showreel Spin, Orbital Room Sweep, Night Fuel Drive, Architect Vision Quest, Unboxed Item Swap, Urban Style Rotation | 9:16 (один 16:9), 7–26 с | 0.4–1.7 с на кадр, 3–31 склейка | 89–144 |

## Как повторить своими средствами
| Что | Наш инструмент | Статус 10.10 |
|---|---|---|
| Замена персонажа/лица в кадре | FaceFusion (пайплайн Bossman, гейты) | работает |
| Замена одежды/объекта | Wan2.1 VACE (`clothes_swap.py`) + сглаживание | пробный: цвет PASS, поза/мерцание — не всё |
| Перенос движения (motion transfer) | Wan2.2-Animate (Apache-2.0) | локально падает (HIP/TDR); на A100 — завтра |
| Смена фона | RVM + SAM2 (`background_swap.py`) | пробный: 3 из 7 порогов |
| Цвет волос / одежды | конструктор Genjutsu (маски SegFormer) | работает, пресеты есть |
| Нарезка по биту, зумы, RGB-split, ramps | `tools/trend_edit/*` (агент собирает) | в работе |

## Схема пресета Motion Studio (предложение)
```json
{"id": "arcade-identity-swap", "name": "...", "kind": "onetake|showreel",
 "driving_clip": "path или library id", "ops": ["replace_character", "motion_transfer", "bg_swap", "outfit", "hair"],
 "character": {"lora": "gfgirl", "refs": ["..."]}, "duration_s": 15, "aspect": "9:16",
 "track": {"bpm": [120, 130], "snap_cuts": true}, "text": [], "gates": ["animation", "face", "background"]}
```
Каждый пресет — запись в каталоге Direct Gen + кнопка в UI + тест гейтов (пороги до прогона).

## Завтра: полосы параллельных агентов (дешёвые облачные модели пишут, Claude проверяет)
| Полоса | Что | Провайдеры | Результат |
|---|---|---|---|
| A. Каталог и UI | схема пресета, 12–15 стартовых пресетов, кнопки в Motion Studio, тесты | GLM 5.3 Flash + Groq gpt-oss | ветка feat/motion-presets |
| B. RunPod Animate | Wan2.2-Animate (replace + motion) на A100 с LoRA персонажей; 3 ведущих клипа из референсов владельца | Mistral Large 4 (код), GLM | 3 видео + замер гейтов |
| C. Шоурил-сборщик | beat-grid, punch-in, speed ramp, RGB-split, текст (уже строится) | GLM, Gemini | пресеты типа B |
| D. Гейты пресетов | пороги ДО теста: личность, анимация, фон, мерцание; отчёты | Groq qwen3.8-27b | tools/video_gate/preset_gate.py |
| E. Исследование трендов | публичные источники → рецепты, лицензии на музыку | Gemini + NVIDIA | docs/owner/TREND_EDIT_RECIPES_RU.md |

Деньги: RunPod A100 ≈ $1.79/ч (лимит вечера ≤ $10, тормозится словом владельца); ключ OpenRouter ≈ $2.2 на всех — только дешёвые модели;
Higgsfield — 0 кредитов, платное не запускается без «да».

## Юридически/этически
Музыка из референсов — только личное использование; публикация требует прав. Лица — только с согласием (девушка владельца).
Каталог Higgsfield — только изучение структуры, их ведущие видео не копируются в продукт.
