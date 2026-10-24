# V2.2 Current Phase Gates

Use only DONE / PARTIAL / FAILED.

| Gate | Required proof |
|---|---|
| Code tools | canonical model-visible `code.*` tools + focused tests |
| Code root safety | outside allowed root is denied |
| Code line truth | fixture confirms source range maps to real file lines |
| Fact runtime | add/search/history/as-of-world/as-of-knowledge tests |
| Fact replacement | old `invalid_at == new.valid_at` |
| No fake invalidation | append mode leaves multiple facts valid |
| SQLite memory | index/search/expand parity |
| Incremental write | one note update does not rewrite whole corpus |
| Safe chunking | fence/table not split under normal limits |
| Snapshot derived | manifest + checksum + safety copy + restore/rebuild semantics |
| browser-use research | measured report + verdict + return trigger |
| OpenClaw research | measured report + control-plane boundary |
| Master plan | counts/statuses consistent |
| README | current test count and phase status current |
| Local tests | full pytest PASS except documented skips |
| JS syntax | all UI JS `node --check` PASS |
| GitHub CI | actual workflow result recorded |
| Secrets | no secrets introduced |

OpenClaw implementation should start only after runtime-integrity gates are DONE and research-only gates have a final verdict.
