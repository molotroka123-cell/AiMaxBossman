# Живая приёмка T2 Studio (ТЗ 2.5 §5 T2 и парный промпт §3.4)

- **Статус**: `PASS`
- **Скрипт приёмки**: `tools/studio_t2_live_acceptance.py`
- **Квитанция**: `docs/audits/2026-10-29-studio-t2-live-acceptance/receipt.json`
- **Облачные расходы**: `$0.00`

## 1. Прогон 1 — Полный цикл заказа (`quoted -> accepted -> render_preset -> qc_passed -> owner_review -> approved -> delivered`)
- **Заказ**: `adba43499d13480bac9d06754fb96ddd` (синтетический клиент, пресет `product_spin`, свой товар без лиц, модель `wan2.2-ti2v-5b`, лицензия `Apache-2.0`).
- **Артефакты и QC**: `contact_sheet.png` (`ac6a7838...`), `qc_report.json` (`sharpness_laplacian = 2427.02`, `flicker_score = 0.0186`, `1080x1920`, `6.0s`, `24 fps`).
- **Себестоимость и маржа**: цена `15.00 USD`, локальная энергосебестоимость `0.000360 USD`, маржа `14.999640 USD`.
- **Ссылка сдачи (`delivery_link`)**:
  - открытие клиентом A: `200 OK`;
  - попытка открытия клиентом B: `404 Not Found`;
  - попытка открытия после `revoke`: `404 Not Found`.

## 2. Прогон 2 — Убитый посреди рендера процесс (`reconcile_required -> resume`) без дубля
- **Заказ**: `cf6139288a2a47bcab7df54d40faa278` (пресет `hand_reveal`).
- **Восстановление**:
  - при аварийном обрыве (`simulate_kill_mid_render=True`) заказ переведён в `reconcile_required`, пауза Джеффа снята (`jeff_paused_after_kill = false`);
  - при повторном запуске `render_preset` рендер продолжен по тому же `job_id` (`a8132d15257b45e3b83e36c0c15d9593`), **`duplicate_jobs_created = 0`**;
  - заказ доведён до `owner_review -> approved -> delivered`.

## 3. Прогон 3 — Живая карточка «Принять / На доработку» в пульте владельца (`:8840`)
- В живом каталоге данных (`C:\Users\asd\AppData\Local\Bossman\CommandCenter\direct-gen\participants\owner\orders\`) создан и подготовлен заказ `e2b65ed971b64923a6e35254662cb369` в статусе `owner_review` с готовым `contact_sheet.png` и `qc_report.json` для решения владельца в мобильном пульте (`Принять` / `На доработку`).
