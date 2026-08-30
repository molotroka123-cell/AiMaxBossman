# IMPROVEMENTS FOR APPROVAL

1. **Teach-by-demonstration**

Записать действия пользователя и превратить их в устойчивый workflow с selectors и expected-state checks.

Decision: `APPROVE / REJECT / LATER`

2. **Selector self-healing**

При изменении UI не угадывать молча: найти вероятный новый selector, поставить PAUSED_NEEDS_REPAIR и запросить approval.

Decision: `APPROVE / REJECT / LATER`

3. **Dry-run simulator**

Показывать будущие действия и затрагиваемые файлы/окна до реального запуска.

Decision: `APPROVE / REJECT / LATER`

4. **Versioned macros**

Каждый workflow имеет версии, diff, rollback и last-known-good.

Decision: `APPROVE / REJECT / LATER`

5. **Triggers without AI**

Расписание, появление файла, запуск программы или hotkey могут запускать детерминированный макрос без LLM.

Decision: `APPROVE / REJECT / LATER`
