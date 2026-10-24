# Numerical models

These CSV files are planning aids, not forecasts.

- `customers_needed_by_arpa.csv`: accounts needed for MRR milestones.
- `osvc_penetration_scenarios.csv`: mechanical penetration examples using the
  March-2026 OSVČ base carried in the research draft; revalidate primary dataset
  before external use.
- `cac_payback_matrix.csv`: sensitivity to ARPA/gross margin/CAC.
- `ltv_sensitivity.csv`: demonstrates how dangerous churn-based LTV assumptions
  can be; compare with capped 12-month contribution.
- `experiment_budget_ladder.csv`: frozen V1.0 internal budget policy.

The agent must never present these outputs as market forecasts without a
venture-specific SAM and acquisition model.

---

## V1.1 AMENDMENT — предупреждения к файлам

### `osvc_penetration_scenarios.csv` — использовать только сверху вниз ПОСЛЕ низа

`02_CZECH_MARKET_BASELINE.md` прямо запрещает брать всю популяцию OSVČ как SAM.
Этот файл делает ровно это: считает MRR от 0.05–1.0 % примерно 1.18 млн OSVČ.
Само его существование создаёт якорь, который правило запрещает.

**Правило V1.1.** Файл разрешён исключительно как проверка снизу вверх:
сначала строится модель «канал → конверсия → платящие аккаунты», и только потом
сюда смотрят, чтобы спросить «какой доле рынка это соответствует и правдоподобна
ли она». Обратный порядок — от процента к выручке — запрещён и в решениях не
принимается.

### `cac_payback_matrix.csv`

Колонка `gross_margin_pct` применяется к цене, а не к чистой выручке.
См. `docs/08_UNIT_ECONOMICS.md`, поправка A-2: при цене s DPH все клетки
завышают вклад примерно на 21 %. До измерения реального CAC (Gate 4) матрица —
источник стоп-условий, а не прогнозов.

### `ltv_sensitivity.csv`

Файл сделан правильно: он показывает, насколько опасна LTV по оттоку на малых
когортах. Ограничение сохраняется — до Gate 3 использовать только колонку
`12m_capped_contribution_CZK`.
