"""Bossman Shorts Factory: isolated app. Finds strong moments in rights-cleared sources, cuts vertical Shorts,
writes titles/hashtags, queues them; publication only after the owner's explicit approval of each item.

Hard rules (enforced in code, not only in docs):
  * no cut, render or queue entry from a source whose rights are not approved by the owner (RightsError);
  * youtube.upload is called only for items in state approved_by_owner, default privacy "private";
  * no soft upscales: output pixels are never enlarged beyond source resolution (blurred fill is decoration only);
  * AI-generated / realistic synthetic content -> contains_synthetic_media=true (YouTube disclosure);
  * every write is role-checked and lands in a hash-chained audit log.
"""
VERSION = "0.1.0"
API_VERSION = "v1"
