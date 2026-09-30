# V2 — состояние оркестрации (ведёт лид; источник истины при потере контекста)

База: коммит ядра f11a6c2, интеграционная ветка feature/bossman-command-center-v2
(локальная), рабочая/push-ветка сессии claude/bossman-control-v03-43igbk.
Worktrees: /home/user/bossman-wt/NN-*, ветки agent/NN-*.

## Волны

| Волна | Агенты | Статус |
|---|---|---|
| 1 | 02 router, 05 forks, 10 skills, 12 resources, 13 kpi | ЗАПУЩЕНА |
| 2 | 01 autopilot, 03 governor, 04 benchlab, 08 reviewer, 14 healing | ожидает |
| 3 | 06 agentmap, 07 worktrees, 09 browser, 11 nl-orchestra, 15 mobile | ожидает |
| интеграция | лид: merge по порядку 12→02→13→10→05→… , проверка каждой | ожидает |
| UI/UX Lead | редизайн (§23–26), после интеграции | ожидает |
| QA | browser QA, persistence/reboot, failure injection, 3 кросс-сценария | ожидает |

## Журнал решений

- Editable-инсталл bcc снят: во всех worktree пакет резолвится из cwd.
- Порты агентов: сервер 91NN, mock'и 92NN/93NN.
- docs/V2_AGENT_BRIEF.md скопирован в worktrees (не коммитится агентами).
