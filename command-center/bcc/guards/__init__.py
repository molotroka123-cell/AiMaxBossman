"""Harness guards (03.11.2026): real failures of the day turned into automatic checks.

G1 foreground  — typing/paste only into the window the action expects (paste into a 2,237-member Telegram group).
G2 long_input  — empty answer on a long input -> one retry on a relevant <=12k excerpt (Jev empty on 40k code).
G3 config_refs — `Module.FIELD` used but never defined (MatchConfig daily caps crashed match rewards).
G4 media_provenance — renamed third-party audio/video caught before upload (six songs with fictional titles).
G5 content_policy — politicians/real people, brands/tobacco/nicotine, crypto, alcohol, gambling in game text.
G6 publish_gate — publish/release only from a committed tree whose build gate and G3/G4/G5 pass.

Pure modules: no network, no keys; the reviewer model of G5 is an injected callable."""
