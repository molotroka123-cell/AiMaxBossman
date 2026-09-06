# Width-fit preview checkpoint

The existing responsive preview now offers **По ширине**. It fits the CSS
viewport to available horizontal space without shrinking a tall page to the
stage height. Vertical overflow remains scrollable. The mode never enlarges
above 100%, changes project HTML, or relaxes iframe isolation.

The existing project-scoped settings retain the mode across reloads. Older
settings remain valid; rolling back this UI causes older parsers to fall back
to their default for this new enum value without overwriting stored settings.

Validation: 17 production JavaScript contract tests passed. The affected Python
selection (`test_web_designer_viewport.py`, `test_web_designer_sandbox_ui.py`,
`test_web_designer.py`) passed 21 tests with 2 browser skips. The expanded live
browser oracle checks width fit, vertical overflow, actual CSS viewport,
persistence and unchanged project history, but was NOT executed because
Chromium is unavailable. Visual acceptance remains unverified.
