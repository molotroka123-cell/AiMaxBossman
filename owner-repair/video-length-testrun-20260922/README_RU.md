# Живой TestRun 1 с — sd.cpp Wan2.2 TI2V-5B, машина владельца, 2026-09-22

Код: ветка `feat/video-duration-presets` (пресет `length=test_1s`). Движок: sd-cli Vulkan, Radeon 8060S, LLM-серверы выгружены.

| | |
|---|---|
| Запрос | `sdcpp:wan2.2-ti2v-5b`, «a paper boat drifting on a calm lake, gentle ripples», `length=test_1s`, seed 7 |
| План после пресета | 640×352, 17 кадров, 16 fps, 16 шагов |
| Результат | completed за ~77 с (движок 80 с по trace) |
| Файл | `testrun_1s.mp4`: H.264 640×352, 17 кадров, 16 fps, 1.0625 с; ffprobe и полное декодирование — OK |
| Провенанс | `mock=false`, backend Vulkan OBSERVED по логу движка, sha256 всех файлов модели expected==observed (`testrun.json`) |
| Содержимое | кадры 0/8/16 — `frames.png`: бумажный кораблик на воде с расходящейся рябью (соответствует промпту, не шум) |

Первая попытка (задача 4) упала: пресет применялся только внутри провайдера, в сохранённом плане оставалось 832×480,
и `persist` отклонил клип 640×352 (`output.width mismatch`). Исправлено: пресет применяется к плану в `validate_plane`;
регрессия `test_testrun_preset_with_default_size_completes_and_plane_matches_output`.

Не проверено вживую: 5 / 10 / 15 / 30 с (по просьбе владельца полный тест отложен). Цепочка сегментов покрыта
тестом с фейковым движком (`test_ten_second_chain_is_two_segments_joined_without_the_repeated_frame`).
