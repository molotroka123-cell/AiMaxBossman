# 09 — Owner checklist / Чек-лист владельца

Only the owner can do these steps. Per the constitution, financing applications, publications, messages to third
parties, account and credential creation, payments and hardware purchases are **always the user's decision**. No agent
performed any of them. Status of every item: **TODO**.

## A. Foundation (blocks almost every program)

1. [ ] Decide the country and form of the legal entity and incorporate it (Inception, Google, Microsoft, AWS all expect
       a company; Inception explicitly requires "officially incorporated").
2. [ ] Register a domain; create a company email on it (Google requires "company email domain matching the website").
       Note: Microsoft sign-up instead requires a **personal** Microsoft account without prior Azure.
3. [ ] Publish a landing page (product, one-pager text from 01 EN, contact). Inception requires "a working website".
4. [ ] Create/complete a LinkedIn profile (Microsoft verification without a referral code).
5. [ ] Decide what reviewers may see: grant read access to the repo or export the 07 artifacts to PDF; remove private
       data (Telegram IDs, participant data, tokens) from anything shared.
6. [ ] Fill the blanks in 01 and 08 (company name, website, email, team).

## B. Evidence to finish before or in parallel (engineering, owner approves)

7. [ ] Reach M1: one complete supervised cycle recorded in the journal (07 G1–G2).
8. [ ] Re-measure IP after `rc19/m-ip` and collect the `rc19/n-self` readiness verdict (07 G9, A11).
9. [ ] Run `tools/coding_value_sim.py` with a real local model and keep the checkpoint (07 G6).
10. [ ] Optional public demo on Hugging Face (fake data only) — publication is owner-gated.

## C. Submissions (in this order; each is a separate owner decision)

11. [ ] NVIDIA Inception — https://programs.nvidia.com/phoenix/application (deck 08, one-pager 01, funding status, incorporation date).
12. [ ] Vast.ai Startup Program — https://vast.ai/startup (ask for temporary 8×H200, 42 node-hours, 03 + 04).
13. [ ] Microsoft for Startups — https://startups.microsoft.com (personal MS account, LinkedIn, **payment card** — read 04 §4 first).
14. [ ] AWS Activate Founders — https://aws.amazon.com/startups/credits (AWS account on paid tier; set budgets on day 1).
15. [ ] Google for Startups Cloud, Start tier — https://cloud.google.com/startup/apply (after website + domain email).
16. [ ] After Inception acceptance: Innovation Lab request in the Inception Portal; ask about DGX Station / GB300 test access.
17. [ ] Look for an accelerator/investor that is an AWS Activate Provider or Microsoft investor-network partner (larger tiers).

## D. Money safety (before any credit is used)

18. [ ] No auto-recharge; prepaid where possible; budget alerts 50/80/100 % on every cloud account.
19. [ ] Put each credit's expiry date in the calendar with reminders at −14 and −3 days; delete GPU resources before expiry
        (Microsoft credits turn into pay-as-you-go).
20. [ ] Set Bossman's daily $ cap and the DeepSeek API cap ($25 for the benchmark, 04 §2); create the API key yourself.

## E. Financing (only after M3 pilots and the M4 benchmark decision)

21. [ ] Request an OEM quote for DGX Station GB300 (ASUS, Dell, Exxact, Gigabyte, HP, MSI, Supermicro) — price is not public.
22. [ ] Compute M from the quote with 05 §3; apply for lease/credit only if pilot margin ≥ 1.5 × M. Owner signs.

## F. After each submission

23. [ ] Record in 06: date, program, what was sent, status (SUBMITTED / ACCEPTED / REJECTED), credit amount and expiry.
