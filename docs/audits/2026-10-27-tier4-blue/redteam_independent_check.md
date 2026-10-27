# Независимая Проверка Red Team Находок (Blue Team Verification)

Дата: 2026-10-27T08:12:00Z  
Ветка: `agent/argon-20261026` (`wt-trio-argon`)  
SHA: `745c99e6a0e7cf22c056343571710ce62336ad84`  
Первоисточник отчёта Red Team: `C:\Users\asd\Bossman\wt-final-20261023\docs\audits\2026-10-27-redteam-self-mod\README_RU.md`  

---

## 1. Проверенные уязвимости Red Team

### Уязвимость 1: Неизолированное исполнение модельных тестов в `pc.self_modify propose` (D-030)
- **Что обнаружил Red Team**: Вызов `propose` запускал `pytest` на произвольном `test_code`, переданном моделью, дочерним процессом под текущей учётной записью пользователя без OS-песочницы.
- **Исправление Blue Team**:
  - Реализован контракт `probe_os_sandbox() -> bool`. На Windows 11 хосте из-за отсутствия `Users`/`ALL APPLICATION PACKAGES` в DACL `python.exe` запуск изолированных процессов (AppContainer, Restricted Token) невозможен.
  - Функция `propose` в `command-center/bcc/features/pc_agent.py` переведена в строгий fail-closed отказ: `status="verification_blocked_unisolated"`, `tests_passed=False`, `approval_id=None`, `pytest_output="NOT_RUN: model-supplied tests require an OS-enforced sandbox."`.
  - Модельный Python-код на хосте не запускается.
  - Регрессионный тест Red Team `test_p4_self_modify_does_not_execute_untrusted_candidate_tests_on_host` — **100% PASS**.

### Уязвимость 2: Обход workspace и повреждение неизменяемого ядра в `pc.self_modify rollback` (D-031)
- **Что обнаружил Red Team**: Параметр `mod_id` не проверялся на path traversal, а записи `applied_files` могли указывать на произвольные файлы системы или неизменяемого ядра.
- **Исправление Blue Team**:
  - `mod_id` валидируется строгим регулярным выражением `^sm-[a-z0-9_-]{1,80}$`.
  - Проверяется закрепление каталога модификации строго внутри `self_mod_dir`.
  - Каждый путь в плане отката валидируется через `is_immutable_core_target` (возвращает отказ `blocked_unsafe_rollback_record`) и проверяется на принадлежность к `workspace_dir` до начала каких-либо изменений на диске.
  - Регрессионный тест Red Team `test_p4_self_modify_rejects_targets_outside_workspace` — **100% PASS**.

### Уязвимость 3: Несоответствие словарей в 3 легаси-тестах STOP (D-032)
- **Что обнаружил Red Team**: Старые тесты проверяли точные ключи ответа STOP и падали из-за появления полезных ключей `pc_agent_cancelled_tasks` и `pc_agent_killed_pids`.
- **Исправление Blue Team**:
  - Обновлены тестовые эталоны в `test_owner_stop_lifecycle.py`, `test_rc_final_integrator_mission_and_security.py` и `test_redteam_rc_20260921.py`.
  - Все 3 теста проходят — **100% PASS**.

---

## 2. Результаты верификации

Все найденные Red Team дефекты полностью подтверждены, локализованы и закрыты безопасными fail-closed механизмами. Ослабления гейтов безопасности ради зелёных тестов не допущено.
