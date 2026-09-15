# NOTICE — AI File Sorter

Bossman's File Intelligence capability drives **AI File Sorter**, a separate
program by the hyperfield project.

| | |
| --- | --- |
| Upstream | <https://github.com/hyperfield/ai-file-sorter> |
| Pinned commit | `4dc374df69b5e63d5354e121097d92e25bbd32da` |
| License | GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later) |
| Relationship | external sidecar, invoked as a subprocess over its documented headless CLI |

## What Bossman does and does not do with it

**Does not** copy, vendor, translate or re-license any AI File Sorter source
into Bossman. No file under `bossman-core/`, `command-center/`, `learning/` or
`bossman_shared/` derives from the upstream C++ tree. The only upstream artefacts
that exist in this repository are this notice, the manifest beside it, and the
protocol constants those two record — the names of command-line flags and JSON
keys, read from upstream source so that Bossman speaks the contract correctly.

**Does** launch an independently installed `aifilesorter` executable as a child
process, pass it typed arguments, and read the JSON files it writes. The two
programs communicate over that process boundary and share no address space, no
build, and no source tree.

## License facts

AI File Sorter is distributed under the AGPL-3.0-or-later. These are the terms
as they stand; the paragraphs below record what the license says and what
follows mechanically, and are not a legal opinion.

- The upstream program's own source remains under AGPL-3.0-or-later wherever it
  is conveyed. Its copyright and license notices must be preserved.
- If a future Bossman distribution **packages, bundles, ships or otherwise
  conveys** the AI File Sorter binary, the AGPL's source-availability
  obligations attach to that conveyance: recipients must be able to obtain the
  corresponding source of the version conveyed, under the same license.
  §19 of the integration brief therefore requires any bootstrap to install
  upstream into a **separate external directory**, never into Bossman's source.
- Bossman does not currently package the binary. The owner installs it, and
  Bossman discovers it. Whether a later packaging step is taken is a decision
  for the project owner, and it carries the obligation above.
- Anything genuinely uncertain here — in particular how the AGPL's network and
  combination provisions apply to any specific future distribution — is a
  question for counsel, not for this file.

## Trademarks

Upstream ships a `TRADEMARKS.md`. Nothing in this integration claims any
upstream mark, and the capability is named "File Intelligence" in Bossman's own
interface rather than after the upstream product.

## Attribution

AI File Sorter is the work of its authors and contributors; see the upstream
repository at the pinned commit for the authoritative list, and its `LICENSE`
file for the full license text.
