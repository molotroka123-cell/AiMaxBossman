export const ROADMAP_END = '2027-01-01T00:00:00Z';

export function projectRoadmap({ now = Date.now(), budget = 0, speed = 1 } = {}) {
  const end = Date.parse(ROADMAP_END);
  const days = Math.max(0, Math.ceil((end - Number(now)) / 86400000));
  const daily = Math.max(0, Number(speed) || 0);
  const totalBudget = Math.max(0, Number(budget) || 0);
  const unitCost = 2; // editable planning assumption; no service call is made
  const scenarios = [
    { id: 'cautious', label: 'Осторожный', factor: 0.6, budgetFactor: 0.5 },
    { id: 'balanced', label: 'Ровный', factor: 1, budgetFactor: 0.75 },
    { id: 'accelerated', label: 'Ускоренный', factor: 1.4, budgetFactor: 1 },
  ];
  return scenarios.map((scenario) => {
    const spendCap = totalBudget * scenario.budgetFactor;
    const workBySpeed = days * daily * scenario.factor;
    const workByBudget = unitCost > 0 ? spendCap / unitCost : null;
    return {
      ...scenario,
      days,
      spendCap,
      workUnits: workByBudget === null ? null : Math.floor(Math.min(workBySpeed, workByBudget)),
      budgetBound: workByBudget !== null && workByBudget < workBySpeed,
    };
  });
}

export function capabilityCounts(nodes = []) {
  return nodes.reduce((counts, node) => {
    const status = String(node?.status || 'unknown');
    counts[status] = (counts[status] || 0) + 1;
    return counts;
  }, {});
}

export function isKnownNumber(value) {
  return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
}
