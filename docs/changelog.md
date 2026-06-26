# Changelog

## [Unreleased] - 2026-06-26

### Architecture
- Refactored `web/app.py` into Flask Blueprint modules under `web/routes/`
  - `analysis.py` — main analysis flow, demo, report download
  - `followup.py` — follow-up Q&A endpoint
  - `history.py` — report history browsing
  - `peers.py` — file-based and Wind-based peer comparison
- Extracted inline JS from templates to `web/static/js/`
  - `workbench.js` — index page interaction logic
  - `report.js` — report page charts, collapsible sections, follow-up Q&A
- Created shared state module `web/shared_state.py`
  - In-memory `REPORT_HISTORY` (deque, maxlen=20) and `REPORT_STATES` (dict)
  - Centralized `save_report_state()` with auto-eviction
- Created template data builder `web/template_data.py`
  - All `AnalysisState -> template data` transformation logic in one place
  - Peer data formatting, indicator simplification, metric block grouping
- Migrated test infrastructure to pytest
- Added project documentation (`docs/architecture.md`, `docs/api.md`)

### Planned
- Fix indicator detail garbled text display
- Fix follow-up Q&A stability
- Enhance macro background with DeepSeek/Wind data
- Optimize investor summary prompt
- Enrich industry comparison dimensions
