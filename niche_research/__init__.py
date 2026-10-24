"""28-day commercial niche research run (owner spec 24.10.2026).

Finds and tests income HYPOTHESES from open data. It never promises revenue and never presents a research
signal as paid demand: every report says «платёжный спрос не проверен» unless an owner-approved buyer contact
happened. Nothing is ever sent, posted or published from this package — it only writes files under its run dir.

Layout:
  config.py        data dir, run config (paid spend is 0 unless the owner sets ``paid_usd_limit``)
  store.py         durable SQLite queue + logs; only the orchestrator holds the enqueue key
  plan.py          4-week niche plan, day/week mapping, scoring criteria
  roles.py         five agent roles (Russian prompt templates); agents get an AgentContext without queue access
  models.py        pluggable model clients + router (local Ollama -> free cloud -> optional capped paid)
  web.py           polite public-page reader (robots.txt, >=5 s per host, UA, no bypass) + search interface
  pii.py           strips emails/phones from anything saved
  orchestrator.py  bounded scheduler tick (deadline, budget, queue checks)
  reports.py       day / week / final reports and improvement proposals (proposals are never applied)
  cli.py           ``python -m niche_research start|tick|status|stop|report``
"""

PAYMENT_DISCLAIMER = "платёжный спрос не проверен"
USER_AGENT = "BossmanNicheResearch/1.0 (+local research; contact: owner of this PC; no login, robots.txt respected)"
RUN_DAYS = 28
MAX_RUNNING_TASKS = 4
