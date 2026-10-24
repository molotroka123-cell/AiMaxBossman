"""Evidence-only website audit + DRAFT-only offer pipeline (scenario D).

Rules baked into the code:
- every finding carries measured evidence; a finding without it is rejected;
- outreach is written as a draft that requires owner approval -- this package
  has no capability to deliver messages anywhere;
- robots.txt disallow is respected and requests are made one at a time.
"""

USER_AGENT = "BossmanSiteAudit/1.0 (+owner contact on request)"
