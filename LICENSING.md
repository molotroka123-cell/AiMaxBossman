# Bossman licensing

Owner decision: 2026-10-11. Bossman-authored code is licensed under the GNU
Affero General Public License version 3 only (`AGPL-3.0-only`). The complete
terms are in [LICENSE](LICENSE); they control over this summary.

This applies to first-party Python packages, the dashboard, tools and the
token-launch-helper. Python distributions carry their own copy of LICENSE.
The license text was obtained from SPDX's `AGPL-3.0-only` license-list-data
entry, matching the GNU AGPL version dated 19 November 2007.

## Existing third-party material

Third-party code, dependencies and vendored assets retain their original
copyrights and licenses. This decision does not relicense them. Preserve the
notices in `integrations/`, `command-center/ui/vendor/`,
`command-center/bcc/skills_catalog/`, `docs/skills/THIRD_PARTY_NOTICES.md` and
`tools/motion_studio/vendor/`, together with dependency distribution notices.
AGPL adoption alone is not a completed dependency compatibility audit.

Model weights, LoRAs, datasets, personal photos/videos, credentials and local
Telegram exports are not licensed by this file and must not enter source or
release archives. Their own terms and permissions still apply.

## Corresponding source

Upstream: https://github.com/molotroka123-cell/AiMaxBossman

The dashboard's source link and Windows archive `LICENSES/SOURCE.md` identify
the exact source commit when that identity is proven. A dirty or unidentified
checkout must not offer an old commit as the complete source of its changes.
Operators distributing modified builds or providing modified network services
must provide the corresponding source of their actual version under AGPL,
including modifications and build/install scripts. See section 13 of LICENSE.

Keep license files and source information in redistributions. A source link
does not certify release quality, third-party compliance or final freeze.
