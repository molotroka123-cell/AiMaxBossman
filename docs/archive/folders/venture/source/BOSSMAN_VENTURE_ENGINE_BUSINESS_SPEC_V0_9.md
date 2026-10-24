# BOSSMAN VENTURE ENGINE
## Business-first specification / ТЗ / Business Plan
### Version 0.9 — foundation only, no implementation code
### Target market for first operating cycle: Czech Republic
### Date: 2026-08-28

---

# 0. Executive decision

BOSSMAN Venture Engine is not another “idea generator”.

It is a business operating layer whose job is to continuously:

1. observe markets and regulatory/technology shifts;
2. identify monetizable problems;
3. quantify them with evidence;
4. reject weak ideas early;
5. prepare a business case;
6. propose a validation plan;
7. after human approval, help launch a paid product;
8. measure actual revenue and unit economics;
9. scale, pivot or kill the venture by explicit numeric gates;
10. retain validated commercial lessons for the next venture.

The engine should eventually become the mechanism through which the agent
system can repeatedly discover and build revenue-producing software products.

The legal actor is always the human entrepreneur / OSVČ / company.
The AI is an operating system and decision-support/automation layer; it is not
the legal owner of money, bank accounts, contracts or liabilities.

This specification intentionally focuses on business logic, evidence quality,
economics, governance and metrics. It does not prescribe implementation code.

---

# 1. Why this feature should exist

The existing agent stack can research, write code, operate tools and learn from
tasks. That is not enough to make money reliably.

The missing layer is a persistent economic objective function.

Without it, an agent tends to optimize for:
- novelty;
- technical elegance;
- feature count;
- completing the requested task.

A venture engine must optimize for:
- verified customer pain;
- willingness to pay;
- speed to first revenue;
- gross margin;
- retention;
- acquisition efficiency;
- low legal/operational risk;
- scalable distribution;
- capital efficiency;
- repeatable learning.

The core transformation is:

`interesting idea -> evidence -> business hypothesis -> paid validation -> product -> recurring cashflow`

rather than:

`interesting idea -> code -> hope`.

---

# 2. Czech market baseline

## 2.1 Addressable entrepreneurial base

As of 31 March 2026, the Czech Social Security Administration reported
1,179,532 active OSVČ, including 690,459 main-activity and 489,073
secondary-activity OSVČ.

This means the first domestic market is large enough that a product does not
need mass-market penetration to become meaningful.

At only 0.1% penetration of the current OSVČ base, a product reaches about
1,180 paying accounts.

At 0.2%, about 2,359 accounts.

At 0.5%, about 5,898 accounts.

This is a central principle for the Venture Engine:
do not require “winner takes all” market assumptions.

## 2.2 SME structure

The European Commission 2025 Czech SME Fact Sheet estimates approximately:

- 1,138,627 SMEs;
- 99.9% of enterprises are SMEs;
- 1,097,528 micro-enterprises, or 96.3% of enterprises;
- SMEs employ about 68.6% of persons employed in the non-financial business
  economy represented in the fact sheet.

Therefore the engine should strongly prioritize micro-business operational
software, not only enterprise AI.

## 2.3 Digitalisation gap

The European Commission’s 2026 Digital Decade report says Czechia has solid
digital foundations but still shows material gaps in SME digitalisation,
advanced-technology adoption, interoperability and data sharing.

That is favorable for vertical software that connects existing fragmented
systems and automates business processes.

## 2.4 Regulatory timing

EET 2.0 is a live example of a regulatory trigger.

Finanční správa published technical information on 5 June 2026 and made the
Playground test environment available on 1 July 2026.

The published project schedule currently targets:
- DIS+ functions in November 2026;
- MOJE EET for small entities in December 2026;
- pilot operation in January 2027;
- production operation in February 2027.

However legislation is not final as of this specification date:
the Senate returned the proposal to the Chamber of Deputies with amendments on
19 August 2026; the Chamber received the Senate documents on 24 August and
further consideration is possible from 4 September 2026.

This is exactly the type of signal Venture Engine should detect:
high economic relevance + implementation window + legal uncertainty.

The engine must distinguish:
“technical implementation schedule exists”
from
“law is final”.

---

# 3. Product identity

Working name:

**BOSSMAN Venture Engine**

Possible later commercial/internal names:
- Venture Brain
- Revenue Foundry
- Profit Hunter
- Opportunity Engine
- Venture OS
- Business Forge

This should be a feature of the agent platform, not a separate generic chatbot.

Its outputs are ventures/products/apps.

It can be used internally first.
There is no requirement to sell Venture Engine itself.

---

# 4. North Star

Primary North Star:

**Portfolio Contribution Margin generated by ventures originated or materially
operated by Venture Engine.**

Secondary North Star:

**Validated Revenue per Unit of Development Effort.**

The engine should never celebrate:
- number of ideas;
- number of features;
- number of code commits;
- number of landing pages;
- model tokens used.

It should celebrate:
- paid pilots;
- collected revenue;
- retained customers;
- gross profit;
- short payback;
- verified market learning.

---

# 5. Business objective hierarchy

## Level 1 — survival

Goal:
reach first externally paid revenue with minimal capital.

Target:
- first paid validation <= 45 days after approval of a venture;
- no venture receives material scale capital before payment evidence;
- avoid fixed payroll before repeatable acquisition.

## Level 2 — repeatability

Goal:
one venture reaches repeatable acquisition and retention.

Example gate:
- 20+ paying customers for low-ticket SaaS, or 5+ paying companies for
  higher-ticket B2B;
- at least two independent acquisition sources or one source proven scalable;
- churn/retention measurable;
- gross margin known from actual invoices/infrastructure/model costs.

## Level 3 — portfolio

Goal:
Venture Engine can launch and kill opportunities systematically.

Target:
- 10–20 opportunities screened per month;
- 2–4 deep-dive cases;
- 1 validation experiment at a time initially;
- maximum 2 active build-stage ventures until revenue supports more.

## Level 4 — company formation / scale

Goal:
move IP, contracts and recurring operations into a dedicated s.r.o. when
economic/operational triggers justify it.

The engine must recommend this based on explicit thresholds, not emotion.

---

# 6. Legal operating model: personal start -> formal business

The clean operating sequence should be:

`research as individual -> commercial validation under proper business status
(OSVČ where applicable) -> dedicated business records and invoicing -> s.r.o.
when trigger conditions are met`

The product must not model recurring paid software activity as “informal money
to a personal account”.

If recurring development/SaaS/services are sold, they should be invoiced and
accounted for under the appropriate Czech business framework.

A dedicated account is operationally recommended even if legal/bank-specific
requirements differ, because Venture Engine needs clean reconciliation of:
- revenue;
- refunds;
- advertising;
- hosting;
- contractors;
- taxes;
- product-level profitability.

The system must support legal-entity abstraction from day one:

`LegalEntity`
- type: individual research / OSVČ / s.r.o.
- IČO
- DIČ if applicable
- VAT status
- invoicing identity
- bank/payment destinations
- tax/accounting export rules
- effective date

This prevents a future s.r.o. migration from requiring a product rewrite.

---

# 7. OSVČ financial planning facts to monitor

Venture Engine should maintain a dated Czech tax/compliance fact sheet.

Examples relevant to 2026:

## VAT

Czech VAT registration rules currently require monitoring two annual domestic
turnover thresholds:

- 2,000,000 CZK;
- 2,536,500 CZK.

The timing of registration differs depending on which threshold is exceeded.

The engine must:
- track rolling/calendar-year taxable domestic turnover according to current law;
- warn before thresholds;
- never rely on a stale hard-coded threshold;
- store source and effective date.

## Income tax

Current general personal-income-tax structure uses:
- 15% on the lower portion of the tax base;
- 23% above the statutory threshold linked to average wage.

Do not use a simplified “15% of revenue” forecast.
Model tax base, expense method and current rules separately.

## Social insurance

ČSSZ publishes annual minimum advances and special rules for new or secondary
OSVČ.

These values change and must be versioned by tax year.

For 2026, ČSSZ currently publishes minimum monthly pension-insurance advances
including:
- main activity: 5,005 CZK from July 2026 under the published 2026 change;
- secondary activity: 1,574 CZK;
- special new-entrant calculation in qualifying circumstances: 3,575 CZK.

There are also rules that can exempt qualifying newly started activity from
advance payments temporarily, while not eliminating final annual liability.

These are planning inputs, not a substitute for an accountant.

---

# 8. s.r.o. transition model

A Czech s.r.o. can have minimum registered capital of 1 CZK, but the economic
reason to create one is not the nominal capital.

Current general corporate income-tax rate is 21%.

Venture Engine should propose a transition review when any of the following
internal business triggers becomes true:

| Trigger | Default review threshold |
|---|---:|
| Recurring MRR | >= 100,000 CZK for 3 consecutive months |
| Annualised revenue | >= 1,200,000 CZK |
| Team | first material long-term contractor/employee |
| Liability | product controls consequential customer operations |
| Enterprise sales | first customer requires company counterparty/DPA/SLA |
| IP portfolio | 2+ products with meaningful revenue |
| Investment | external investor/partner/equity discussion |
| VAT proximity | projected turnover approaches statutory threshold |
| Risk isolation | personal liability materially exceeds acceptable level |
| Acquisition | product/company could reasonably be sold |

These are management thresholds, not legal mandates.

The engine should produce a “OSVČ vs s.r.o. migration memo” when triggered:
- revenue;
- profit;
- taxes;
- accounting overhead;
- liability;
- contracts;
- IP;
- VAT;
- expected hiring;
- customer expectations;
- cost of migration;
- recommendation;
- confidence.

Human/accountant/lawyer approval remains required.

---

# 9. What Venture Engine is allowed to do

## AUTO

The engine may autonomously:
- search public market information;
- monitor official Czech/EU regulatory sources;
- monitor competitor public websites and pricing;
- analyse public reviews/forums;
- query public business registries/APIs within their terms;
- build market maps;
- estimate scenarios;
- rank opportunities;
- draft product specifications;
- draft landing-page copy;
- draft pricing experiments;
- produce SEO/content plans;
- analyse first-party product analytics;
- detect churn/risk;
- draft responses to inbound customer enquiries;
- calculate unit economics;
- recommend kill/pivot/scale actions.

## ASK

Explicit human approval should be required before:
- publishing a new product publicly;
- buying a domain;
- starting paid ads;
- increasing an approved ad budget;
- sending any outbound commercial message;
- creating a paid external account;
- signing or accepting third-party contractual terms;
- issuing material refunds;
- offering bespoke contractual discounts outside allowed bands;
- sending invoices before legal/business identity is configured;
- hiring contractors;
- purchasing data;
- transferring money;
- deploying a product that processes sensitive customer data;
- launching physical-world or regulated actions.

## DENY by default

The engine must not autonomously:
- open bank/payment accounts;
- borrow money;
- invest customer/company funds;
- transfer money to arbitrary destinations;
- sign legal contracts as if it were a legal person;
- create fake reviews/testimonials;
- impersonate a customer or government official;
- send scraped-email cold spam;
- bypass website terms/access controls;
- falsify business metrics;
- hide uncertainty from the owner;
- automatically change tax status;
- automatically form/dissolve a legal entity;
- create misleading “revenue” using owner-funded circular payments.

---

# 10. Czech outbound-marketing constraint

This is a critical design rule.

Czech ÚOOÚ guidance states that a public e-mail address found on the Internet
cannot simply be used to send an unsolicited advertising offer.

It also states that sending an e-mail merely asking for consent to receive
commercial communications is itself not a valid workaround.

Existing customers may receive commercial communications about the sender’s own
similar products/services under the statutory conditions, including a clear
opt-out.

A response to a concrete incoming request/enquiry is treated differently from
unsolicited marketing when it actually responds to that request.

Therefore Venture Engine must never define:

`scrape 10,000 Czech emails -> AI cold-email campaign`

as an acceptable growth strategy.

Preferred acquisition channels:
- SEO;
- opt-in content/lead magnets;
- paid search/social ads after approval;
- partner/reseller channels;
- marketplaces;
- referral;
- integrations;
- events/associations;
- inbound RFQ/tender response;
- existing-customer cross-sell under compliant rules;
- manual sales processes reviewed for applicable law.

The engine should maintain:
- consent provenance;
- opt-out/Robinson list;
- campaign legal basis;
- message source;
- customer-status evidence;
- suppression list.

---

# 11. Evidence hierarchy

Every important claim must be tagged.

## Evidence A — primary official

Examples:
- Finanční správa
- ČSSZ
- MPO
- ČSÚ
- Czech Parliament / Senate
- gov.cz
- EU Commission
- official API documentation

Weight: 1.00

## Evidence B — primary commercial

Examples:
- competitor pricing pages;
- product documentation;
- public terms;
- public customer case studies;
- marketplace listings.

Weight: 0.85

## Evidence C — direct market evidence

Examples:
- paid pilot;
- signed LOI;
- customer interview;
- conversion test;
- product usage;
- cancellation reason;
- sales call.

Weight depends on sample quality.
A real payment is stronger than a survey answer.

## Evidence D — community/secondary

Examples:
- Reddit/forum discussions;
- reviews;
- blogs;
- press articles.

Useful for hypothesis generation, not enough for major capital decisions alone.

## Evidence E — assumption

Anything without external support.

The engine must never silently promote an assumption to a fact.

---

# 12. Opportunity object

Every opportunity should be represented by one durable business object.

Required fields:

- Opportunity ID
- name
- date discovered
- market/country
- vertical
- customer type
- problem statement
- existing workaround
- trigger
- urgency
- frequency
- willingness-to-pay hypothesis
- estimated buyer
- user
- payer
- beneficiary
- market size sources
- competitor list
- pricing references
- distribution hypotheses
- regulatory constraints
- data availability
- integration requirements
- technical complexity
- estimated MVP effort
- estimated operating cost
- support burden
- gross-margin hypothesis
- retention hypothesis
- expansion hypothesis
- defensibility
- failure modes
- evidence ledger
- confidence
- score
- stage
- next experiment
- kill criteria
- actual revenue
- actual cost
- retrospective.

No opportunity should advance without this object.

---

# 13. Opportunity scoring model

The engine should use a 0–10 score for each dimension.

Recommended weights:

| Dimension | Weight |
|---|---:|
| Pain severity | 15% |
| Proven willingness to pay | 13% |
| Regulatory/market trigger | 10% |
| Distribution accessibility | 11% |
| Automation advantage | 10% |
| Time to first revenue | 9% |
| Gross-margin potential | 7% |
| Retention/repeat frequency | 7% |
| Competitive whitespace | 6% |
| Data/integration accessibility | 5% |
| Defensibility | 4% |
| Founder/portfolio strategic fit | 3% |

Base score = weighted average.

Then apply a confidence multiplier:

- 1.00: direct paid evidence + strong primary data;
- 0.90: interviews + primary data + competitor evidence;
- 0.80: primary data and strong indirect evidence;
- 0.70: mostly secondary evidence;
- 0.60: hypothesis-heavy;
- <=0.50: speculation.

Final Opportunity Score:

`base score × confidence multiplier`

Hard blockers override the score.

Examples of hard blockers:
- unlawful planned distribution;
- required data unavailable;
- unit economics structurally negative;
- need for licensing the team cannot obtain;
- expected liability disproportionate to upside;
- market already commoditized below sustainable cost;
- no identifiable payer;
- no plausible path to first payment.

---

# 14. Required score bands

| Final score | Default action |
|---|---|
| 8.0–10.0 | immediate deep validation |
| 7.0–7.99 | validate when slot available |
| 6.0–6.99 | monitor / improve evidence |
| 5.0–5.99 | park |
| <5.0 | reject unless a new trigger appears |

No more than three high-scoring opportunities should compete for active build
capacity at one time.

The agent should show why an idea scored badly.
Do not hide weak points behind one composite number.

---

# 15. Trigger engine

Venture Engine should monitor five trigger classes.

## Regulatory triggers

Examples:
- EET;
- VAT changes;
- new reporting obligation;
- cybersecurity obligations;
- transport/tachograph changes;
- energy flexibility;
- grants;
- permits.

Commercial advantage:
customers may be forced to change behavior.

## Technology triggers

Examples:
- local models become cheap enough;
- a public API opens;
- OCR/CAD/vision improves;
- an incumbent changes pricing;
- a platform launches an integration.

## Cost triggers

Examples:
- labour cost rising;
- energy cost volatility;
- expensive manual compliance;
- high software seat costs.

## Fragmentation triggers

Examples:
- workflow requires 4–6 disconnected tools;
- data repeatedly retyped;
- PDF/email/Excel handoffs dominate.

## Distribution triggers

Examples:
- new marketplace;
- government developer programme;
- association partnership;
- public tender feed;
- new channel.

---

# 16. Market-selection doctrine

The first portfolio should prefer markets with:

- Czech-specific pain;
- measurable economic value;
- low initial regulatory capital;
- software gross margin;
- fragmented incumbent tools;
- ability to pilot with <10 customers;
- clear payer;
- short sales cycle;
- self-serve or owner-led sale;
- limited integration count for MVP;
- recurring operational usage.

Avoid initially:
- consumer social apps;
- pure ad-supported products;
- businesses requiring major inventory;
- heavily licensed financial/medical decision systems;
- markets where buyer is a ministry and sales cycles are 12–24 months;
- products that require huge proprietary datasets before first value;
- “AI wrapper” products with no workflow ownership.

---

# 17. Venture archetypes

Venture Engine should classify every candidate.

## A. Micro-SaaS

Typical:
- 199–999 CZK/month;
- self-serve;
- high volume;
- low support;
- SEO/paid acquisition.

## B. Vertical SMB SaaS

Typical:
- 1,000–10,000 CZK/month;
- onboarding;
- integrations;
- lower customer count;
- higher switching cost.

## C. Usage-based AI

Typical:
base fee + consumption.
The model must include inference/document/compute cost explicitly.

## D. Transaction/revenue-share

Only if legal and payment architecture is clear.

## E. Service-to-software

Start with manually-assisted service to prove demand;
automate the repeated workflow later.

This is often the fastest path to real revenue.

## F. Data/monitoring product

Recurring subscription for alerts, risk or compliance.

## G. Internal product later spun out

Useful when the team already has a real operational problem.

---

# 18. Unit economics model

Every venture must calculate actual, not theoretical:

## Revenue

- MRR
- ARR
- one-time implementation
- usage revenue
- services
- refunds
- discounts
- net revenue

## Variable cost

- model inference
- OCR
- storage
- bandwidth
- e-mail/SMS
- payment fees
- third-party APIs
- customer-specific infrastructure
- support labor attributable to account

## Contribution margin

`Revenue - variable cost`

## Gross margin %

`Contribution margin / Revenue`

Initial target bands:

| Product type | Target gross margin |
|---|---:|
| low-compute SaaS | >85% |
| AI-heavy SaaS | >70% initially; >80% mature |
| service-assisted software | >50% pilot; rising with automation |
| pure service | not preferred unless it proves software demand |

The engine must not label model/API cost as “fixed infrastructure” if it scales
with usage.

---

# 19. CAC rules

Track CAC by channel.

`CAC = acquisition-channel spend / new paying customers attributable to channel`

Include:
- ad spend;
- affiliate commission;
- sales contractor;
- sponsorship;
- paid content;
- discounts used as acquisition subsidy.

Do not count unpaid owner time as zero forever.
Maintain “cash CAC” and “fully-loaded CAC”.

Target for low-ticket SaaS:
- payback <= 4 months preferred;
- <= 6 months acceptable after retention proof;
- >9 months requires strong retention/expansion evidence.

For enterprise B2B:
longer payback may be acceptable, but only with signed contract economics.

---

# 20. LTV discipline

Do not calculate fantasy LTV from one month of churn.

Early stage:
use conservative horizon-capped LTV.

Example:
`12-month contribution LTV`, not infinite theoretical LTV.

Only after sufficient cohorts should the engine use:
`ARPA × gross margin / monthly churn`

and it must report cohort size and confidence.

Target mature LTV:CAC:
- >=3.0 acceptable;
- >=4.0 strong;
- <2.0 not scalable without correction.

---

# 21. Pricing engine

Price should be based on:
- economic value;
- alternative cost;
- incumbent benchmarks;
- buyer budget;
- support burden;
- complexity;
- switching friction.

Do not use model-token cost + arbitrary markup as the primary pricing method.

Test:
- value metric;
- package boundaries;
- annual discount;
- free trial vs freemium;
- onboarding fee;
- usage limit;
- team seats.

Every pricing change should record:
- cohort;
- conversion;
- ARPA;
- support load;
- refund rate;
- churn.

---

# 22. Current Czech software pricing benchmark

Current public Czech invoicing/accounting tools demonstrate that the baseline
“simple invoicing” category is already cheap.

Examples observed during this research:

Fakturoid:
- free tier;
- roughly 151 CZK/month entry paid tier;
- 211 CZK/month automation tier;
- 393 CZK/month maximum tier.

mPohoda:
- free limited tier;
- Basic approximately 166 CZK/month on annual billing;
- Pro approximately 298 CZK/month on annual billing.

iDoklad:
- free tier;
- about 187 CZK/month annual-plan equivalent for Basic;
- 358 CZK/month Popular;
- 625 CZK/month Premium.

Vyfakturuj:
- free tier;
- 175 CZK/month Mini;
- 299 CZK/month Ideal;
- 620 CZK/month Profi.

Conclusion:

A new Czech product should not enter the market as:
“AI invoicing, 299 CZK”.

It must own a broader workflow and produce measurable operational value.

For ŽivnoPilot-like products, the wedge should be:
`job -> quote -> calendar -> work -> payment -> invoice/EET -> expense -> accountant`

rather than only:
`invoice`.

---

# 23. Validation ladder

No code-first validation.

## Stage 0 — evidence scan

Duration:
1–3 days.

Budget:
near zero.

Output:
opportunity score and major unknowns.

## Stage 1 — problem proof

Goal:
prove that target users experience the problem.

Evidence targets:
- 10–20 high-quality interviews, or
- equivalent inbound/rfq/first-party evidence.

Pass:
>=60% of qualified interviewees independently describe the target pain and an
existing workaround/cost.

Do not lead respondents with the product idea.

## Stage 2 — payment proof

Goal:
prove willingness to pay.

Methods:
- paid pilot;
- deposit;
- pre-order where appropriate;
- signed paid trial;
- service-assisted fulfillment.

Pass:
at least 3 independent paying customers for micro-SaaS/SMB concept,
or one meaningful paid B2B pilot if deal size is high.

A “yes, sounds useful” survey is not payment proof.

## Stage 3 — repeat usage

Pass example:
- at least 50% of paid pilot customers use the core workflow repeatedly;
- clear activation event identified;
- support burden recorded.

## Stage 4 — acquisition proof

Pass:
one channel can produce customers at acceptable payback.

## Stage 5 — scale

Only now increase spend/build complexity.

---

# 24. Development capital allocation

Initial internal policy:

## Discovery

Maximum cash spend per opportunity:
1,000 CZK without special approval.

## Validation

Default experiment budget:
5,000 CZK.

## Paid pilot/MVP

Default cap before payment evidence:
20,000 CZK external cash cost.

## Scale

Any single venture cumulative external spend >50,000 CZK should require a
business-review packet showing:
- revenue;
- CAC;
- retention;
- margin;
- forecast;
- downside.

This is not because 50k is inherently large.
It forces capital discipline.

---

# 25. Time allocation

At personal/OSVČ stage:

- maximum one active build-stage venture;
- one secondary research-stage candidate;
- all others parked/monitored.

Once portfolio contribution margin covers infrastructure + contractor budget:
allow two concurrent build-stage ventures.

The engine must penalise “shiny-object switching”.

---

# 26. Kill criteria

The engine needs authority to recommend STOP.

Default kill/rework triggers:

- no identifiable payer after 10 qualified conversations;
- no paid pilot after 30 high-quality sales opportunities where product and
  offer were actually presented;
- acquisition channel payback forecast >9 months for low-ticket SaaS;
- gross margin structurally <60% without strategic reason;
- core workflow used less than twice by most intended repeat users;
- support burden >15 minutes/customer/week after onboarding for a supposed
  self-service product;
- legal blocker eliminates planned distribution;
- required data/API is unavailable;
- incumbent gives same value free and no differentiator exists;
- founder/agent repeatedly adds features instead of improving conversion;
- product requires bespoke development for every customer;
- customer acquisition depends on unlawful unsolicited messaging.

Before killing, Venture Engine should distinguish:
- problem wrong;
- segment wrong;
- offer wrong;
- price wrong;
- channel wrong;
- implementation wrong.

---

# 27. Pivot policy

A pivot is allowed if new evidence improves the expected economics.

A pivot must create a new hypothesis version.

Never retroactively rewrite the original hypothesis as if it had always been
correct.

Record:
- previous thesis;
- evidence that failed;
- new thesis;
- changed target;
- expected metric impact;
- new kill date.

---

# 28. Distribution engine

The agent should score channels by:

- reach;
- intent;
- CAC;
- conversion;
- legal risk;
- setup time;
- scalability;
- attribution quality.

Preferred early channels in Czech micro-B2B:

1. search intent / SEO;
2. paid search with hard budget;
3. niche Facebook/industry communities through compliant participation;
4. associations and partner accountants;
5. integrations with existing software;
6. referral;
7. local content;
8. marketplaces;
9. inbound requests and public tenders where relevant.

Do not default to mass outbound email.

---

# 29. Sales funnel model

Every venture should define funnel states.

Example:

`visitor -> lead -> qualified -> demo/trial -> activated -> paid -> retained`

Track:
- visitors;
- lead conversion;
- qualification rate;
- demo/trial rate;
- activation;
- paid conversion;
- time to pay;
- retention.

The engine should detect whether the bottleneck is:
traffic, offer, onboarding, value or retention.

---

# 30. Default funnel targets for early experiments

These are operating targets, not market facts.

Landing page:
- <2% relevant visitor -> lead after 500+ qualified visits: investigate offer;
- 2–5%: plausible;
- >5%: strong for a high-intent niche.

Trial:
- activation target >=40%;
- trial-to-paid >=15% baseline target for self-serve niche software;
- >=25% strong if traffic is well qualified.

Paid:
- month-1 retained >=80% target;
- support tickets per active account should fall over time.

Do not kill on tiny samples.
Report confidence intervals/sample size.

---

# 31. Revenue milestones

Portfolio milestone ladder:

## M0
first real external 1 CZK.

Purpose:
prove payment system + legal/invoice path.

## M1
10,000 CZK cumulative revenue.

Purpose:
prove strangers/real customers pay.

## M2
25,000 CZK MRR.

Purpose:
prove repeatable early demand.

## M3
50,000 CZK MRR.

Purpose:
fund basic infrastructure and experiments.

## M4
100,000 CZK MRR.

Purpose:
s.r.o. migration review if not already done; stronger operational controls.

## M5
250,000 CZK MRR.

Purpose:
formal portfolio/team planning.

## M6
500,000 CZK MRR.

Purpose:
build repeatable sales/support operations.

## M7
1,000,000 CZK MRR.

Purpose:
company-level management, product portfolio governance, security/compliance
maturity and potential cross-border scale.

The engine must show net contribution, not only MRR.

---

# 32. Portfolio P&L

Every venture must have independent accounting dimensions.

Income:
- subscriptions;
- setup fees;
- usage;
- services.

Direct costs:
- infra;
- AI/API;
- payment;
- SMS/e-mail;
- support;
- data.

Allocated costs:
- shared servers;
- contractor;
- legal/accounting;
- marketing shared.

Output:
- revenue;
- contribution;
- gross margin;
- operating contribution;
- cash burn;
- runway;
- cumulative cash invested;
- payback date.

A profitable portfolio can contain one experimental loss-making venture, but
the engine must show that explicitly.

---

# 33. Cash-control rules

The agent may observe and reconcile money.
It should not have unilateral treasury authority.

Recommended model:

- inbound payments can be automatically reconciled;
- refunds <= predefined low-risk threshold may later be delegated, but default ASK;
- outbound payments ASK;
- new beneficiary ASK;
- recurring subscription purchase ASK;
- ad budget ASK and capped;
- tax payment prepared, never sent without explicit confirmation;
- bank credentials stay outside model context.

The engine should maintain:
`approved_budget`, `committed`, `spent`, `remaining`.

---

# 34. Fraud / metric-integrity rules

Forbidden:
- owner pays own product to fabricate traction;
- bot signups counted as leads;
- free accounts counted as paid;
- gross bookings reported as revenue when refunded;
- ad coupon credited as organic profit;
- duplicated customers across legal entities;
- cherry-picking best cohort while hiding total churn.

Every KPI should have a provenance query.

---

# 35. Agent roles

Venture Engine should conceptually have distinct roles even if one model performs
several.

## Scout

Finds triggers and opportunities.

## Market Analyst

Quantifies market, competition, pain.

## CFO

Builds unit economics, scenario model, cash plan.

## Regulatory Analyst

Checks law, effective dates, regulatory uncertainty.

## Product Strategist

Defines wedge, MVP and expansion.

## Distribution Strategist

Defines legal acquisition channels.

## Devil’s Advocate

Attempts to kill the idea.

## Experiment Designer

Defines cheapest falsifiable validation.

## Venture Reviewer

Makes GO / HOLD / KILL recommendation.

## Operator

After approval, manages metrics and alerts.

No role may mark its own unsupported assertion as verified evidence.

---

# 36. Decision committee

Before build, Opus/strong reasoning model should receive a fixed Business
Decision Packet.

Required packet:

1. one-sentence opportunity;
2. exact target customer;
3. buyer/payer/user;
4. quantified pain;
5. trigger;
6. market size with source;
7. competitor/pricing table;
8. current workaround;
9. differentiation;
10. acquisition strategy;
11. legal constraints;
12. MVP scope;
13. estimated build effort;
14. 12-month unit-economics model;
15. downside case;
16. best case;
17. kill criteria;
18. validation experiment;
19. confidence;
20. final recommendation.

The reviewer must explicitly answer:
“What evidence would make me change my mind?”

---

# 37. Scenario model

Each venture must have three scenarios.

## Bear

Assume:
- slower acquisition;
- lower conversion;
- lower ARPA;
- higher churn;
- higher support;
- higher AI cost.

## Base

Most defensible current assumptions.

## Bull

Upside, but still physically plausible.

No business decision should use only the Bull case.

---

# 38. Example scenario framework for low-ticket Czech SaaS

Assume blended ARPA target:
450 CZK/month.

This is not a factual market average; it is a planning example positioned above
basic invoice-only products because the product must own a broader workflow.

Using the Q1 2026 OSVČ base of 1,179,532:

| Penetration | Paying accounts | MRR at 450 CZK | ARR |
|---:|---:|---:|---:|
| 0.1% | ~1,180 | ~531k CZK | ~6.37m CZK |
| 0.2% | ~2,359 | ~1.06m CZK | ~12.74m CZK |
| 0.5% | ~5,898 | ~2.65m CZK | ~31.85m CZK |
| 1.0% | ~11,795 | ~5.31m CZK | ~63.69m CZK |

This is deliberately a penetration model, not a claim that every OSVČ is a
qualified customer.

Venture Engine must subsequently narrow SAM by profession/use case.

---

# 39. ŽivnoPilot as calibration opportunity

ŽivnoPilot is useful as a test case for Venture Engine because:
- Czech-local;
- large OSVČ/microbusiness base;
- fragmented workflow;
- EET regulatory trigger;
- strong incumbent invoicing competition;
- opportunity exists outside invoice creation.

Core promise:

`Zakázka -> peníze -> faktura -> EET -> náklady -> účetní`

Potential initial target:
field-service / appointment-based microbusinesses where owner is both producer
and administrator.

Examples:
- electricians;
- plumbers;
- repair trades;
- installers;
- cleaners;
- beauty;
- small auto service;
- independent technicians.

Do not start with “all Czech OSVČ”.

---

# 40. ŽivnoPilot competitor conclusion

Existing Czech tools already cover:
- invoices;
- contacts;
- bank matching;
- reminders;
- tax/VAT support;
- expense capture;
- API;
- some offer/order/project functions.

Therefore ŽivnoPilot should compete on workflow ownership:

`voice/photo/message -> structured job -> quote -> schedule -> completion ->
payment -> fiscal/accounting trail`

not on:
“we also create invoices”.

---

# 41. ŽivnoPilot pricing hypotheses to test

Do not freeze pricing before interviews.

Test cells:

## Solo

249 vs 349 CZK/month.

## Pro

499 vs 699 CZK/month.

## Team

999 vs 1,490 CZK/month.

Potential free tier:
only if it creates cheap acquisition and does not create support burden.

Possible trial:
14–30 days.

Do not make EET alone the paid differentiator because the state plans MOJE EET
for small entities.

Charge for:
- workflow;
- time saved;
- customer follow-up;
- scheduling;
- job profitability;
- automation;
- accounting handoff;
- team operations.

---

# 42. ŽivnoPilot MVP business hypothesis

The MVP should prove one core economic chain:

`new customer/job -> quote -> work -> payment/invoice`

Optional regulatory EET integration should not be allowed to delay proving
workflow demand.

Primary value metric:
hours of administration saved + faster collection + fewer forgotten jobs.

MVP business success is not:
“EET API call works”.

It is:
“a user pays because the whole job-to-cash loop is easier”.

---

# 43. ŽivnoPilot activation event

Suggested activation:

User:
- creates/imports first customer;
- creates a job;
- sends/produces a quote;
- marks job complete;
- creates/records payment or invoice.

Time-to-value target:
<15 minutes from signup to useful first workflow.

The Venture Engine should measure the exact step where users drop.

---

# 44. ŽivnoPilot retention event

A retained account should perform a real operational action each week/month,
not merely log in.

Examples:
- create job;
- complete job;
- issue document;
- record payment;
- schedule work.

Monthly login alone is weak evidence.

---

# 45. Initial ŽivnoPilot 12-month targets

This is a management target set, not a forecast.

By day 30:
- 15+ qualified interviews;
- 3+ users willing to pilot;
- first explicit price feedback.

By day 60:
- first paid pilots;
- core job-to-cash workflow manually/semi-manually proven.

By day 90:
- 10 paying accounts OR a clear pivot;
- activation >=40%;
- identifiable support burden.

By month 6:
- 50–100 paying accounts if low-ticket self-serve;
- blended ARPA known;
- one repeatable acquisition source.

By month 12:
target decision between:
- scale;
- niche pivot;
- kill.

Strong scale signal:
100k CZK MRR with acceptable retention/payback.

---

# 46. Venture Engine first-year objective

The engine itself should not be judged only on ŽivnoPilot.

12-month system objective:

- screen >=100 opportunities;
- create >=20 deep Business Decision Packets;
- run >=8 real market experiments;
- obtain paid validation for >=3 concepts;
- launch <=3 products;
- kill weak products quickly;
- get at least one product to meaningful recurring revenue.

A reasonable stretch portfolio objective:
100,000–250,000 CZK combined MRR after 12 months.

This is a target, not a guaranteed forecast.

---

# 47. Research budget

Start lean.

Example first 6-month cash envelope:

| Category | Monthly planning cap |
|---|---:|
| domains/tools | 2,000 CZK |
| hosting/API experiments | 5,000 CZK |
| ads/validation | 10,000 CZK |
| accounting/legal reserve | 5,000 CZK |
| design/content/contractor | 8,000 CZK |
| contingency | 5,000 CZK |
| Total planning ceiling | 35,000 CZK/month |

Actual spending should be below the ceiling until payment proof exists.

Do not spend 35k simply because budget exists.

---

# 48. Opportunity discovery sources

Venture Engine should maintain a Czech source registry.

Primary:

- ARES / Ministry of Finance public services;
- MPO statistics;
- ČSSZ open data;
- ČSÚ;
- Finanční správa;
- Parliament/Senate;
- gov.cz;
- public procurement systems;
- EU Commission / Eurostat;
- regulator pages;
- energy/transport/industry authorities.

Commercial:
- competitor pricing;
- changelogs;
- terms;
- app stores;
- review platforms;
- marketplace listings.

Demand:
- search trends;
- forums;
- niche communities;
- job postings;
- public RFQs;
- customer support issues;
- own portfolio telemetry.

Each source needs:
- source type;
- access method;
- allowed use;
- refresh frequency;
- trust;
- date.

---

# 49. Public registry policy

Public availability does not mean unlimited marketing permission.

ARES and similar data can support:
- market sizing;
- company verification;
- segmentation;
- due diligence.

They must not automatically become:
“email everybody in registry”.

Data use and outreach are separate decisions.

---

# 50. Opportunity monitoring cadence

Daily:
- high-impact regulatory watch;
- critical competitor changes;
- portfolio anomalies.

Weekly:
- new opportunities;
- pricing changes;
- distribution signals.

Monthly:
- full opportunity ranking;
- portfolio allocation.

Quarterly:
- market thesis review;
- legal-entity review;
- product kill/scale review.

---

# 51. Metrics dashboard

Portfolio:

- total MRR;
- total ARR;
- contribution margin;
- cash;
- monthly burn;
- runway;
- active ventures;
- experiments;
- revenue by venture;
- revenue by channel;
- concentration risk.

Per venture:

- leads;
- qualified;
- activation;
- paid conversion;
- ARPA;
- MRR;
- churn;
- retention;
- CAC;
- payback;
- gross margin;
- support minutes/account;
- AI cost/account;
- refund rate;
- NPS/CSAT only as secondary metrics.

Research:

- opportunities found;
- opportunities rejected;
- average confidence;
- time from discovery to decision;
- false-positive retrospective.

---

# 52. Alert thresholds

The engine should not spam.

Alert only when actionable.

Examples:
- MRR -10% MoM;
- churn > target by material amount;
- CAC payback >6 months;
- gross margin falls >5 pp;
- API/model cost/account rises >20%;
- conversion drops >30%;
- VAT threshold forecast crossed within 90 days;
- new regulatory deadline;
- competitor cuts price >25%;
- critical data source/API changes;
- cash runway <6 months;
- one customer >25% of venture revenue;
- one channel >70% of new customers.

---

# 53. Customer concentration

At early B2B stage, concentration may be unavoidable.

The engine should track:
- top 1 customer % of revenue;
- top 5 %;
- contract expiry;
- payment delay.

Warning:
top customer >25% MRR.

Critical:
top customer >40% without contractual security.

---

# 54. Receivables

For invoice-based B2B:

Track:
- issued;
- due;
- overdue;
- DSO;
- expected cash date;
- disputed invoices.

Revenue is not the same as cash.

Venture Engine should maintain cash-basis and accrual-style operational views.

---

# 55. Data retention / privacy economics

Privacy is also a cost variable.

Every venture should minimize:
- personal data;
- sensitive data;
- raw media;
- indefinite logs.

Before pursuing a market, quantify:
- DPA burden;
- security burden;
- breach impact;
- hosting constraints;
- support/access controls.

A slightly smaller market with low data sensitivity may be a better first
venture than a bigger regulated market.

---

# 56. AI cost governance

For each AI feature record:

- model;
- tokens/input;
- tokens/output;
- requests/account;
- cache hit rate;
- local/cloud;
- cost/request;
- cost/active account;
- latency;
- failure rate.

Agent should propose cheaper model/local routing only if KPI quality stays above
threshold.

Do not optimize token cost while breaking retention.

---

# 57. Build effort economics

Development effort should be valued.

For comparison use:
`Engineering Cost Equivalent`.

Even if development is done internally, assign a shadow cost.

Suggested internal planning value:
one focused engineering day = configurable CZK-equivalent.

This prevents the engine from treating 60 days of development as “free”.

Opportunity score should penalise time-to-revenue.

---

# 58. Feature ROI

Every major feature proposal should include:

- target metric;
- expected lift;
- evidence;
- implementation effort;
- ongoing cost;
- rollback criterion.

Feature accepted if:
expected contribution gain justifies effort/risk.

Do not add features because competitor has them.

---

# 59. Product moat taxonomy

Score defensibility based on:

- workflow depth;
- integrations;
- customer data history;
- calibrated models;
- proprietary operational dataset;
- network effects;
- distribution partnerships;
- compliance expertise;
- switching costs;
- brand/trust.

“Uses AI” is not a moat.

---

# 60. Human approval UX

Every ASK action should show:
- action;
- external party;
- cash amount;
- legal effect;
- data leaving system;
- expected business outcome;
- downside;
- reversible? yes/no.

Example:

“Approve 3,000 CZK Google Ads experiment for 7 days?
Goal: 30 qualified leads.
Kill if CPL >250 CZK after 1,500 CZK spend.”

That is useful approval.
“Approve marketing?” is not.

---

# 61. Experiment ledger

Every experiment needs:

- hypothesis;
- metric;
- start;
- end;
- max budget;
- sample target;
- success threshold;
- failure threshold;
- actual result;
- confidence;
- decision;
- lesson.

Do not run an experiment without a predefined failure criterion.

---

# 62. Revenue attribution

Use:
- first touch;
- last touch;
- self-reported source;
- campaign;
- partner;
- organic.

At small scale, self-reported attribution is valuable.

Do not pretend perfect multi-touch attribution exists with tiny data.

---

# 63. Cohort model

Retention and economics should be shown by signup/payment cohort.

At minimum:
- month 0;
- month 1;
- month 2;
- month 3;
- month 6;
- month 12.

Do not average new and old customers into one misleading churn number.

---

# 64. Churn taxonomy

Cancellation should be classified:
- no value;
- too expensive;
- missing feature;
- business closed;
- seasonal;
- switched competitor;
- technical issue;
- support issue;
- compliance concern;
- accidental/payment failure.

The agent should prioritize churn causes by lost MRR, not count only.

---

# 65. Support as product research

Every support ticket should be tagged:
- bug;
- UX;
- missing workflow;
- education;
- integration;
- billing.

Repeated support issue can create a product opportunity.

But Venture Engine should not automatically turn every request into roadmap.

---

# 66. Partnership model

Partners can lower CAC.

Potential Czech microbusiness partners:
- accountants;
- tax advisers;
- industry associations;
- hardware/service suppliers;
- banks/payment providers;
- POS vendors;
- trade schools.

Partner economics:
- fixed referral;
- recurring share;
- bundle;
- white-label.

All require explicit contract approval.

---

# 67. Referral model

A referral programme should only launch after product satisfaction is real.

Track:
- invites;
- converted referrals;
- reward cost;
- referred retention;
- fraud.

Avoid giving rewards before paid/retained customer event.

---

# 68. SEO economics

For SEO candidate:
- keyword intent;
- estimated value;
- content cost;
- conversion;
- time-to-rank;
- competitor authority.

Do not write 500 AI pages without demonstrated search intent/quality.

Programmatic SEO requires quality gate.

---

# 69. Paid acquisition

Campaign-level controls:

- max daily spend;
- max total spend;
- target CPL/CAC;
- negative keyword monitoring;
- fraud;
- geographic scope;
- landing page version.

Agent can recommend optimization.
Budget increase requires approval unless an explicit bounded policy is granted.

---

# 70. Product-led growth

Use only where product can deliver value before sales.

Track:
- signup;
- activation;
- aha moment;
- collaboration/invite;
- conversion.

Freemium is not automatically good.
If free users create support/AI cost without conversion, remove or restrict it.

---

# 71. Service-assisted validation

For complicated B2B ideas, the fastest validation can be a concierge service.

Example:
instead of building a full automated compliance engine, manually deliver the
report to first 5 paying customers with AI support.

Then measure:
- willingness to pay;
- repeated steps;
- automation candidates;
- support burden.

Software is built after the workflow repeats.

---

# 72. Procurement policy

Before buying an API/tool:
- why needed;
- free alternative;
- variable cost;
- lock-in;
- data export;
- privacy;
- cancellation;
- price increase risk.

Every recurring SaaS dependency should belong to a venture cost center.

---

# 73. Vendor concentration

Warning if one external vendor can disable the entire product.

Critical vendors need:
- backup path;
- export;
- fallback model;
- cached operation;
- contract/SLA if scale justifies.

---

# 74. Cross-border expansion

Do not expand to Slovakia/EU only because Czech growth feels slow.

Expansion gate:
- Czech ICP proven;
- onboarding localized;
- core economics positive;
- legal differences mapped;
- support capacity available.

Then score country based on:
- market similarity;
- language;
- regulatory overlap;
- CAC;
- competition.

---

# 75. Intellectual property

From day one, track:
- source ownership;
- contractor assignment;
- datasets;
- third-party licenses;
- trademarks/domain;
- generated assets;
- model licenses.

Before s.r.o. migration:
create IP transfer/licensing plan.

Do not leave commercial IP ownership ambiguous.

---

# 76. Security maturity by revenue

## Pre-revenue

- secrets management;
- backups;
- least privilege;
- basic logs.

## 25k MRR

- incident procedure;
- dependency monitoring;
- access audit;
- DPA templates where needed.

## 100k MRR

- structured security review;
- disaster-recovery test;
- customer data inventory;
- vendor risk.

## Enterprise / sensitive data

- contractual/security requirements assessed before sale.

Do not build enterprise compliance theatre before product-market proof, but do
not ignore basic security.

---

# 77. Business continuity

Every venture should document:
- what happens if model unavailable;
- API unavailable;
- owner unavailable;
- server down;
- payment provider down.

Where possible:
graceful degraded mode.

---

# 78. Accounting handoff

Venture Engine should create monthly exports:

- revenue by product;
- invoices;
- refunds;
- payment fees;
- marketing;
- software expenses;
- contractor expenses;
- VAT-relevant classification;
- outstanding receivables.

It should not attempt to replace professional accounting advice on uncertain
transactions.

---

# 79. Audit trail

Every money-relevant or legal-relevant decision needs:
- actor/model;
- timestamp;
- source;
- proposal;
- approval;
- execution result.

This is required for future autonomy.

---

# 80. Model confidence

The agent must report:
- confidence score;
- evidence count;
- contradictory evidence;
- missing data.

High confidence without evidence is a defect.

---

# 81. Hallucination defense

Critical market facts need cross-checking.

Examples:
- market size;
- tax threshold;
- legal deadline;
- competitor price;
- licensing requirement.

At least one primary source when available.

If sources conflict:
show conflict and do not choose silently.

---

# 82. Regulatory watch as business source

Regulation is both risk and opportunity.

Each regulatory change should be classified:
- creates mandatory spend;
- creates reporting burden;
- creates data/API;
- removes requirement;
- shifts deadline;
- creates exemption.

Then Venture Engine asks:
“Who now has a new expensive workflow?”

This is a key Czech-market discovery mechanism.

---

# 83. Venture idea output format

Every proposed app should be presented with:

- Name
- Vertical
- Customer
- Pain
- Why now
- What they do today
- Product wedge
- Why AI matters
- Why AI is not enough
- Market evidence
- Competition
- Price
- CAC hypothesis
- retention hypothesis
- margin
- build time
- risks
- legal
- validation
- 90-day milestones
- score
- recommendation.

No vague “cool idea”.

---

# 84. Monthly venture review

Review agenda:

1. cash;
2. revenue;
3. margin;
4. retention;
5. acquisition;
6. product;
7. risk;
8. experiments;
9. opportunity pipeline;
10. kill/scale decisions.

Outcome:
capital/time reallocated.

---

# 85. Quarterly board-style memo

Even as one-person operation, create discipline.

Memo:
- portfolio performance;
- what was believed 90 days ago;
- what evidence changed;
- revenue;
- failures;
- strongest opportunity;
- capital allocation;
- legal/tax triggers;
- next quarter targets.

This becomes training data for better future business decisions.

---

# 86. Business-learning memory

Store durable lessons only when supported by evidence.

Good memory:
“Czech electricians interviewed in experiment X cared more about quote-to-job
workflow than invoice design; 7/10 cited schedule/change tracking.”

Bad memory:
“Electricians love AI.”

Durable business memory fields:
- segment;
- experiment;
- sample;
- result;
- date;
- confidence;
- applicable scope.

---

# 87. Skill creation

Repeated successful commercial behavior may become a skill.

Examples:
- Czech regulatory opportunity scan;
- competitor pricing audit;
- paid pilot design;
- micro-SaaS economics review;
- churn retrospective.

Skill promotion must use the existing skill evaluation process.

Do not give a “sales skill” permission to spam.

---

# 88. Autonomous mode maturity

## Level 0 — analyst

Research + recommendations only.

## Level 1 — experiment assistant

Creates assets/drafts; human executes external actions.

## Level 2 — bounded operator

May operate approved campaigns/budgets under fixed ceilings.

## Level 3 — portfolio operator

Can reallocate small pre-approved budgets and run experiments within policy.

## Level 4 — high autonomy

Only after months of audit history and reliable financial controls.

Even Level 4:
bank transfers, loans, legal formation and major contracts remain approval-bound.

---

# 89. First operating phase recommendation

Do not immediately implement full autonomous revenue execution.

Phase 1 should be the business brain:

- market radar;
- opportunity objects;
- evidence ledger;
- scoring;
- business-plan generator;
- scenario model;
- experiment design;
- KPI definitions;
- approval rules;
- monthly portfolio review.

No banking automation.

No cold outbound.

No automatic legal action.

No uncontrolled ad spend.

This is the correct “foundation only” scope.

---

# 90. Foundation deliverables for the later ZIP

When we package the specification, the ZIP should contain documentation and
schemas/examples, but no large implementation.

Recommended contents:

1. `00_READ_FIRST.md`
2. `MASTER_PROMPT_FOR_OPUS.md`
3. `VENTURE_ENGINE_PRODUCT_SPEC.md`
4. `CZECH_MARKET_BASELINE.md`
5. `OPPORTUNITY_SCORING_MODEL.md`
6. `EVIDENCE_POLICY.md`
7. `STAGE_GATES.md`
8. `UNIT_ECONOMICS.md`
9. `LEGAL_ENTITY_OSVC_TO_SRO.md`
10. `CZECH_OUTREACH_COMPLIANCE.md`
11. `CAPITAL_ALLOCATION_POLICY.md`
12. `APPROVAL_MATRIX.md`
13. `PORTFOLIO_KPIS.md`
14. `EXPERIMENT_LEDGER_TEMPLATE.md`
15. `BUSINESS_DECISION_PACKET_TEMPLATE.md`
16. `MONTHLY_VENTURE_REVIEW_TEMPLATE.md`
17. `QUARTERLY_BOARD_MEMO_TEMPLATE.md`
18. `ZIVNOPILOT_CALIBRATION_CASE.md`
19. `SOURCES_AND_FRESHNESS_POLICY.md`
20. `ACCEPTANCE_GATES.md`

Optional machine-readable examples:
- opportunity schema;
- experiment schema;
- venture KPI schema;
- legal entity configuration schema.

Still no implementation code in this foundation package.

---

# 91. Opus mission

Opus should not be asked:
“Come up with businesses.”

It should be asked:

“Given the current evidence, rank opportunities by expected risk-adjusted
contribution margin per development month. Identify the cheapest experiment
that can falsify the top hypothesis. Do not approve build until payment evidence
or an explicitly justified exception exists.”

This framing matters.

---

# 92. Opus adversarial questions

For every idea Opus must answer:

- Why would customer pay rather than continue current workaround?
- Why now?
- What incumbent can copy this?
- What does the product do without AI?
- Is AI actually needed?
- Who owns the budget?
- How do we legally reach customers?
- What is the first payment event?
- What happens if CAC doubles?
- What happens if model cost triples?
- What if regulation is delayed/cancelled?
- What if target market is half our estimate?
- What metric kills the idea?
- What proof is missing?

No GO unless these have satisfactory answers.

---

# 93. Capital-return metric

Useful portfolio metric:

**R&D Cash Multiple**

`cumulative contribution margin / cumulative external development + validation cash`

Early target:
>0 is already proof.

Mature target:
>3x over an agreed period before aggressive scaling.

Also track development-month return:
`annualised contribution / engineering-months invested`.

---

# 94. Risk-adjusted expected value

For comparison, not accounting:

`EV = probability of reaching target × expected 24-month contribution
     - validation/build capital
     - expected risk cost`

Probability must come from score/evidence band, not made-up precision.

Use ranges.

Example:
20–35%, not 27.43%.

---

# 95. Speed metric

**Time to Revenue (TTR)**

From owner approval of validation:
to first real external payment.

Target:
<=45 days for micro-SaaS/service-to-software.
Longer requires justification.

This is one of Venture Engine’s strongest anti-overbuilding metrics.

---

# 96. Experiment efficiency

**Learning per CZK**

Every experiment should state:
“What high-value uncertainty does this money remove?”

Bad:
spend 20k on branding before demand proof.

Good:
spend 3k to test whether qualified buyers book a demo at target price.

---

# 97. Customer-value metric

Where possible, calculate ROI for customer.

Examples:
- admin hours saved;
- revenue recovered;
- faster payment;
- reduced no-shows;
- lower compliance labor;
- reduced downtime.

Price should be a fraction of captured value.

If value cannot be quantified, selling is harder.

---

# 98. Minimum evidence for “market exists”

Require at least two categories:

- official/market data;
- competitor spending/revenue/pricing signal;
- direct buyer interviews;
- paid validation;
- search intent;
- observed manual workaround.

One Reddit thread is not a market.

---

# 99. Minimum evidence for “AI advantage”

Require one of:

- quality impossible with rules alone;
- major labor reduction;
- unstructured input converted to structured workflow;
- prediction/classification has measured value;
- natural-language interface materially reduces friction.

If simple deterministic software solves the problem better, use simple software.

---

# 100. Final acceptance gates for Venture Engine foundation

The foundation is complete only if all are true:

- business objective is economic, not technical;
- evidence hierarchy exists;
- Czech market baseline is dated;
- legal/regulatory freshness policy exists;
- opportunity score exists;
- confidence multiplier exists;
- hard blockers exist;
- stage gates exist;
- payment proof required before scale;
- kill criteria exist;
- pivot history preserved;
- unit economics defined;
- CAC/payback/LTV defined;
- contribution margin defined;
- portfolio P&L defined;
- cash controls defined;
- approval matrix defined;
- spam/outreach restriction defined;
- OSVČ -> s.r.o. transition triggers defined;
- VAT monitoring defined;
- tax values are versioned, not timeless constants;
- model/API cost tracked;
- support cost tracked;
- market research provenance required;
- Opus decision packet defined;
- adversarial reviewer required;
- experiment ledger defined;
- portfolio review cadence defined;
- learning memory rules defined;
- autonomous maturity levels defined;
- first operating phase explicitly excludes uncontrolled financial autonomy;
- ŽivnoPilot can be evaluated as a calibration case without hard-wiring the
  Venture Engine to one product.

---

# 101. What we still need to decide before freezing V1.0

These are configuration decisions, not missing architecture:

## A. Risk appetite

Recommended default:
capital-efficient / conservative.

## B. Validation cash limit

Recommended initial:
5,000 CZK per ordinary experiment.

## C. Pre-revenue build cash cap

Recommended:
20,000 CZK per venture without escalation.

## D. Geographic scope

Recommended:
Czech Republic first.
Slovakia/EU only after one repeatable Czech motion.

## E. Outbound autonomy

Recommended:
none initially.
Draft only; human approval for external communication.

## F. Ads autonomy

Recommended:
agent may optimize within an explicitly approved campaign cap later, but cannot
open/increase budget autonomously initially.

## G. Company transition

Recommended:
automatic review at 100k MRR for 3 months or earlier if liability/enterprise
contract/IP trigger occurs.

## H. First calibration venture

Recommended:
ŽivnoPilot remains a candidate, not a predetermined winner.
Venture Engine should be allowed to score it against other Czech opportunities.

---

# 102. Source register used for this draft

Primary/official sources consulted, current as of 2026-08-28:

1. Česká správa sociálního zabezpečení — Přehled o počtu OSVČ, stav
   k 31.03.2026.
2. European Commission — 2025 SME Country Fact Sheet: Czechia.
3. European Commission — Czechia 2026 Digital Decade Country Report.
4. Finanční správa — EET 2.0 technical preparation, 05.06.2026.
5. Finanční správa — EET 2.0 Playground, 01.07.2026.
6. Poslanecká sněmovna — Sněmovní tisk 189, current legislative history.
7. Finanční správa — VAT registration changes effective from 2025 and current
   system description.
8. ČSSZ — OSVČ obligations and 2026 advance amounts.
9. BusinessInfo.cz — Czech business/trade setup and s.r.o. formation.
10. Finanční správa / gov.cz — corporate income-tax rate.
11. ÚOOÚ — commercial communications / public e-mail / customer opt-out rules.
12. Ministry of Finance — ARES public API technical documentation.
13. Fakturoid — current public pricing.
14. iDoklad — current public pricing.
15. mPohoda — current public pricing.
16. Vyfakturuj — current public pricing.

All future numeric claims should retain:
source, observed date, effective date where relevant, and freshness status.

---

# 103. Final recommendation

Do not build Venture Engine as a “fully autonomous company bot” first.

Build the economic constitution first.

The first version should make the agent unusually disciplined at:
- finding real problems;
- proving numbers;
- challenging assumptions;
- designing cheap validation;
- ranking capital allocation;
- tracking revenue;
- killing weak ideas.

Only after the engine has produced multiple real paid outcomes should it receive
more authority over advertising, external communication or cash.

The long-term target is not an agent that “can start businesses”.

The target is an agent that has a demonstrably better process for deciding
which businesses deserve to exist, can prove that with real customer payments,
and can operate them inside explicit legal and financial boundaries.

That is the foundation from which autonomous earning can be added safely and
profitably.
