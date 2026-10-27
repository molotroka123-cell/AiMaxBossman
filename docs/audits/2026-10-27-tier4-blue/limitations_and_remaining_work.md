# Ограничения, Остатки и План Развития (Tier 4 Assessment)

Дата: 2026-10-27T08:12:00Z  
Оценка зрелости: **Зрелый внутренний Tier 3 / Частичный Tier 4 (с честными fail-closed защитами)**  
Обязательное заключительное утверждение: **«Bossman движется к production; production/freeze/Tier 5 пока не доказан»**  

---

## 1. Честный учёт ограничений (Zero Hallucinations)

1. **OS-Sandbox на Windows 11**:
   - На текущем Windows 11 хосте DACL на `python.exe` содержит только `SYSTEM`, `Administrators`, `asd` (отсутствуют `Users` и `ALL APPLICATION PACKAGES`).
   - AppContainer и deny-only Restricted Token получают `STATUS_ACCESS_DENIED` при загрузке `python312.dll`.
   - Low Integrity без deny-only не защищает пользовательские секреты (`NO_READ_UP` отсутствует) и сокеты `afd.sys`.
   - Решение: исполнение кандидатов `pc.self_modify` переведено в безопасный fail-closed режим `NOT_RUN` (`verification_blocked_unisolated`). Тесты не запускаются до появления реальной изоляции (Docker/WSL на Windows или bwrap/runsc на Linux).

2. **Аппаратная приёмка железа (Hardware Acceptance)**:
   - Отчёт `tools/owner_machine_report.py` фиксирует характеристики (AMD Ryzen AI MAX+ 395, 120 ГБ ОЗУ, 100% моделей в VRAM), но не заменяет собой полную приёмку `scripts/target_hardware_acceptance.py`.
   - Камера не подключена в данном окружении (`NOT_MEASURED`).

3. **Бизнес-траектории (Offline Capability vs Real Revenue)**:
   - War Terminal tender dossier проверен на статических фикстурах без выхода в сеть (`revenue: NOT_RUN`).
   - SwapMe CRM проверен на синтетических курсах и изолированной SQLite БД ($0.00 реальных транзакций).
   - QLoRA дообучение остановлено ($0.00 затрат), поды не создавались.

---

## 2. Необходимые шаги для достижения полного Tier 5 / Production

1. Развернуть контейнерную изоляцию (Docker/Podman/WSL2 на Windows или bwrap на Linux) для обеспечения выполнения OS-sandbox контракта.
2. Подключить и откалибровать физическую веб-камеру через `ai-webcam-vision`.
3. Подключить живые боевые фиды открытых данных (ISVZ/Prozorro/CNB) после прохождения внешнего юридического аудита.
4. Провести холдаут-турнир моделей Base vs Defensive QLoRA.
