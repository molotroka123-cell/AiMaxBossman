# Ограничения, Остатки и План Развития (Tier 4 Consolidated Assessment)

- **Дата**: `2026-10-27T09:30:00Z`
- **Проверенный SHA кода и тестов**: `25653d0cad43fe594ea7bc7fe1157d4dc03997b3`
- **Оценка зрелости**: **Зрелый внутренний Tier 3 / Частичный Tier 4 (с честными fail-closed защитами и восстановленным строгим секрет-сканером)**
- **Обязательное заключительное утверждение**: **«Bossman движется к production; production/freeze/Tier 5 пока не доказан»**

---

## 1. Честный учёт ограничений (Zero Hallucinations)

1. **OS-Sandbox на Windows 11 (`pc.self_modify`)**:
   - На текущем Windows 11 хосте DACL на `python.exe` содержит только `SYSTEM`, `Administrators`, `asd` (отсутствуют `Users` и `ALL APPLICATION PACKAGES`).
   - AppContainer и deny-only Restricted Token получают `STATUS_ACCESS_DENIED (0xC0000022)` при загрузке `python312.dll`.
   - Low Integrity без deny-only не защищает пользовательские секреты (`NO_READ_UP` отсутствует) и сокеты `afd.sys`.
   - **Решение в коде (`25653d0c`)**: `probe_os_sandbox()` возвращает `False`; исполнение кандидатов `pc.self_modify` находится в строгом fail-closed режиме `NOT_RUN` (`status="verification_blocked_unisolated"`, `tests_passed=False`, `approval_id=None`, `applied_files=[]`). Ни `approved=True`, ни `apply_after_approval=True` не могут обойти это ограничение. Дополнительно добавлена защита `_has_reparse_or_symlink_below` от побега через симлинки и NTFS junctions при `propose`, `apply` и `rollback`.

2. **Аппаратная приёмка железа (Hardware Acceptance)**:
   - Отчёт `tools/owner_machine_report.py` фиксирует характеристики машины владельца (AMD Ryzen AI MAX+ 395, 120 ГБ ОЗУ), но не заменяет полную аппаратную приёмку `scripts/target_hardware_acceptance.py`.
   - Физическая веб-камера не подключена (`NOT_RUN` / `NOT_MEASURED`).

3. **Бизнес-траектории (Offline Capability vs Real Revenue)**:
   - War Terminal tender dossier проверен на статических фикстурах без выхода в сеть (`revenue: NOT_RUN`).
   - SwapMe CRM проверен на синтетических курсах и изолированной SQLite БД ($0.00 реальных транзакций).
   - QLoRA дообучение остановлено ($0.00 затрат), поды не создавались, холдаут-турнир `NOT_RUN`.

---

## 2. Необходимые шаги для достижения полного Tier 5 / Production

1. Развернуть контейнерную изоляцию (WSL2/Docker/Podman на Windows или `bwrap`/`runsc` на Linux) с закрытой сетью (`--unshare-net` / `--network=none`), read-only rootfs и лимитами CPU/RAM для включения исполнения модельных тестов в `pc.self_modify`.
2. Подключить и откалибровать физическую веб-камеру для закрытия направления `Devices / Cam`.
3. Подключить живые фиды открытых данных (ISVZ/Prozorro/CNB) после согласования владельцем.
4. Провести холдаут-турнир моделей Base vs Defensive QLoRA.
