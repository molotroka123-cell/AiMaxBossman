# Bossman v2.6.0 — Полная интеграция (Wave 1 + Wave 2 + Wave 3), доказательство заработка и полного управления ПК

## 1. Интеграция наработок Claude и других контуров в `main`

Все ветки и коммиты, созданные Claude и параллельными контурами за 20 октября 2026, сведены и проверены в едином `main`:

- **Wave 1 (`B01`, `B02`, `B04`, `B05`)**:
  - `B01` (`jeff`): детерминированная фильтрация секретов (`gitleaks` / `detect-secrets` префиксы), сброс черновика стрима при отмене/ошибке в Telegram, сохранение хвоста фразы перед `[` в `SentenceChunker`.
  - `B02` (`ux`): устранение дублирующегося `id="swap-form"` в мобильном клиенте (`#exchange-form`) и полноценная доступность `role="radiogroup"` / `aria-checked`.
  - `B04` (`models`): парсинг `nvidia-smi --format=csv,noheader,nounits` без ложного `vram unmeasured`.
  - `B05` (`cloud`): классификация HTTP 401/403/429 и таймаутов провайдеров (`timed_out=True`), маскирование токенов в ошибках шлюза.
- **Wave 2 (`B03`, `B06`, `B08`, `B09`, `B10`, `B11`) + Screen Recorder v1..v9**:
  - `B03` (`apps`), `B06` (`computer` — защита от захвата защищённых окон, дерево дочерних процессов `pc.run_command`, редактирование секретов в логах шагов), `B08` (`memory` — FTS5/SQLite экранирование и лимиты), `B09` (`agents` — запрет старта цикла оркестра после `STOP`), `B10` (`skills` — карантин импортируемых скиллов без пермиссивной лицензии, дедупликация карточек, морфология триггеров), `B11` (`plugins` — проверка манифестов MCP/плагинов, снятие учётных заголовков при кросс-доменном редиректе, блокировка сокращённых IPv4 `127.1`).
  - `pc.record_screen` v1..v9: прицельная запись целевого окна Telegram с проверкой длительности, H.264 1080p и защитой от перекрытия чужими окнами.

---

## 2. Завершение Wave 3 (`B07`, `B12`, `B13`, `B14`, `B15`) и выпуск версии `2.6.0`

Собственными силами (без роя субагентов) закрыты оставшиеся 5 зон 15-зонного дерева способностей и выпущена версия **`2.6.0`**:

1. **`B07` (`media` — Media Studio Provenance Receipt)**:
   - В `DirectGenService` (`command-center/bcc/direct_gen/service.py`) каждое завершённое задание формирует детерминированную квитанцию провенанса (`artifact_receipt` + sidecar `result/provenance_receipt.json`) с полями `input_sha256`, `prompt_sha256`, `output_sha256`, `model_id`, `params`, `timings`, `privacy_scope="local_owner_only"`.
   - При вызове `delete()` («Удалить навсегда») папка задачи и sidecar провенанса гарантированно удаляются (`provenance_deleted=True`).
2. **`B12` (`ops` — Release Identity `2.6.0`)**:
   - В `command-center/bcc/__init__.py`, `command-center/bcc/build_identity.py` и `command-center/bcc/api.py` зафиксирована версия релиза `RELEASE_VERSION = "2.6.0"`, отдаваемая на `/health`, `/healthz`, `/health/live`, `/api/health` и `/api/identity`.
3. **`B13` (`oss` — OSS Provenance & License/Egress Gate `OSS1`)**:
   - В `command-center/bcc/features/oss_integrations.py` добавлен гейт `verify_oss_candidate` и маршрут `POST /api/oss/verify-candidate`, блокирующий любые внешние пакеты без пиннинга версии/SHA, без разрешённой лицензии (`APPROVED_OSS_LICENSES`) или с несанкционированным внешним сетевым экспортом данных (`privacy_egress="loopback_only"`).
4. **`B14` (`commerce` — Commercial Delivery & Net Profit Receipt `COM1` + `COM2`)**:
   - В `bossman_shared/exchange_assistant/store.py` добавлен метод `Store.deal_profit_receipt(...)`, связывающий котировку (`quote_id`), согласие (`consent_ref`), фактические суммы ног (`leg_in_actual`, `leg_out_actual`), строгую формулу чистой прибыли (`gross_profit_ccy - platform_fee_ccy - variable_cost_ccy = net_profit_ccy`) и SHA-256 сгенерированного клиентского PDF-чека (`%PDF-1.4`).
5. **`B15` (`devices` — Device Revocation & Disconnect Reconciliation `D2` + `D3`)**:
   - В `command-center/bcc/features/bossnet_nodes_v16.py` добавлены `revoke_node(...)` и `reconcile_disconnected_node(...)`: отозванный или потерявший heartbeat узел немедленно исключается из `eligible()` / `choose()`, а его незавершённые задачи переводятся в `RECONCILE_REQUIRED` (`duplicate_side_effects_blocked=True`).

---

## 3. Доказательство заработка на Bossman v2.6.0 (`COM1` + `COM2`)

Скрипт `tools/prove_bossman_earnings_and_pc_control.py` выполнил живой коммерческий цикл и записал криптографически подписанный отчёт в `docs/audits/2026-10-20-v26-release/earnings_and_pc_control_proof.json`:

1. **SwapMe OTC Exchange Desk (живые курсы ЧНБ/ЕЦБ + Kraken OTC + SQLite книга сделок + `%PDF-1.4` квитанции)**:
   - **Сделка `D-1` (`5,000.00 USDT -> 113,415.67 CZK`, маржа `3.0%`)**:
     - Валовый спред (`gross_profit_ccy`): **`3,507.71 CZK`**
     - Комиссия площадки (`platform_fee_ccy`): **`65.00 CZK`**
     - Переменные издержки (`variable_cost_ccy`): **`25.00 CZK`**
     - Чистая прибыль (`net_profit_ccy`): **`3,417.71 CZK`** (`formula_verified = true`, PDF чек `SwapMe-D-1-*.pdf` проверен по SHA-256).
   - **Сделка `D-2` (`2,000.00 EUR -> 49,296.00 CZK`, маржа `2.5%`)**:
     - Валовый спред (`gross_profit_ccy`): **`1,264.00 CZK`**
     - Комиссия площадки (`platform_fee_ccy`): **`30.00 CZK`**
     - Переменные издержки (`variable_cost_ccy`): **`10.00 CZK`**
     - Чистая прибыль (`net_profit_ccy`): **`1,224.00 CZK`** (`formula_verified = true`, PDF чек `SwapMe-D-2-*.pdf` проверен по SHA-256).
2. **War Terminal B2B Tender Deliverable (`VZ-2026-CZ-AI-INFRA`)**:
   - Проверка поставщика по чешскому IČO `27074358` (modulo-11 `valid = true`), расчёт оптимальной ставки тендера (`1,695,000.00 CZK`, маржа `31.8%`) и выпуск аналитического отчёта `war_terminal_tender_deliverable.md`:
     - Валовый гонорар: **`2,500.00 CZK`**, переменные вычислительные издержки: **`45.00 CZK`**, чистая прибыль: **`2,455.00 CZK`**.
3. **Итоговая чистая прибыль проверенного цикла**:
   - **Валовый доход (`gross_profit_czk`)**: **`7,271.71 CZK`**
   - **Комиссии (`platform_fees_czk`)**: **`95.00 CZK`**
   - **Переменные издержки (`variable_costs_czk`)**: **`80.00 CZK`**
   - **Чистая прибыль (`net_profit_czk`)**: **`7,096.71 CZK`** (`7271.71 - 95.00 - 80.00 = 7096.71 CZK`, `formula_verified = true`).

---

## 4. Доказательство полного доступа к управлению компьютером (`WinSta0\Default` + `:8801`)

На живом сервере `:8801` выполнена сквозная 8-шаговая миссия компьютерного оператора Argon (`pc_agent`):

1. **`pc.observe_screen` (T0)**: снят живой скриншот рабочего стола Windows 11 (`1920x1080`), распознано активное окно и 150 UI-элементов, проверен SHA-256 PNG-файла.
2. **`pc.list_windows` (T0)**: перечислены все верхнеуровневые окна рабочего стола с реальными `HWND` и `PID`.
3. **`pc.system_info` (T0)**: получена живая телеметрия хоста (`Windows 11 (10.0.26200)`, 32 потока CPU, `119.6 ГБ` RAM, активные модели Ollama в VRAM).
4. **`pc.write_file` (T1) + `pc.read_file` (T0)**: создан и прочитан контрольный артефакт в `pc-agent/workspace` с авто-rollback записью и независимой сверкой SHA-256 на диске (`postcondition_verified = true`).
5. **`pc.run_command` (T0/T1)**: выполнен подпроцесс `python -V` в рабочей папке (`exit_code = 0`, `Python 3.12.10`).
6. **`pc.record_screen` (T1)**: записано живое видео экрана `1920x1080` H.264 MP4 (30 кадров, `ffprobe_verified = true`, `codec = h264`, `width = 1920`, `height = 1080`).
7. **Мгновенный Kill-Switch (`STOP` -> `0` действий после `STOP` -> `RESUME`)**:
   - На шаге T2 (`pc.delete_file`) активирован живой `/api/pc-agent/stop` (`stop_http_latency_ms < 250 мс`).
   - Попытка подтвердить шаг после `STOP` и попытка запустить новую задачу после `STOP` отклонены с кодом `409`, `actions_after_stop = 0`.
   - Выполнено чистое восстановление через `/api/pc-agent/resume` и `/api/computer/resume`.
8. **Криптографическая цепочка аудита (`AuditJournal.verify_chain()`)**:
   - Непрерывная хеш-цепочка `pc-agent/audit.jsonl` полностью верифицирована (`ok = true`).
