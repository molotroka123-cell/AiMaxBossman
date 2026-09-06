/* Pure view-model helpers. Scheduling facts are not post-state evidence. */
export function missionControls(status) {
  return ({
    draft: ['start', 'stop'], queued: ['start', 'pause', 'stop'],
    planning: ['stop'], running: ['pause', 'stop'], paused: ['resume', 'stop'],
  })[status] || [];
}

export function continuityLabel(child) {
  if (!child || typeof child.reason_code !== 'string') return 'Состояние зависимостей неизвестно';
  const labels = {
    READY: 'Зависимости пройдены; запуск ещё не доказывает результат',
    DEPENDENCIES_PENDING: 'Ждёт завершения шагов', DEPENDENCY_FAILED: 'Предыдущий шаг не завершён успешно',
    CHILD_TERMINAL: 'Шаг завершён или остановлен; автоматического повтора нет',
    MISSION_PAUSED: 'Миссия на паузе', MISSION_QUEUED: 'Миссия ждёт запуска',
    MISSION_CANCELLED: 'Миссия остановлена', MISSION_FAILED: 'Миссия завершилась с ошибкой',
    MISSION_COMPLETED: 'Миссия завершена', CHILD_CONTRACT_CHANGED: 'Изменён контракт шага — запуск запрещён',
    PLAN_BINDING_INVALID: 'Привязка плана не подтверждена', PLAN_CHILDREN_CHANGED: 'Изменён состав шагов',
    LEGACY_DAG_UNBOUND: 'Старый план требует явной миграции зависимостей',
  };
  const base = labels[child.reason_code] || 'Запуск заблокирован; проверьте состояние миссии';
  const waiting = Array.isArray(child.waiting_for) ? child.waiting_for.filter(Number.isSafeInteger) : [];
  return waiting.length ? `${base}: ${waiting.map(i => `#${i}`).join(', ')}` : base;
}

export function progressFraction(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.min(1, Math.max(0, numeric)) : 0;
}
