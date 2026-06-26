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

### Bug Fixes (Phase 2)
- Removed Jinja `[:80]`/`[:120]` truncation that cut UTF-8 mid-char → CSS line-clamp instead
- Added conversation history to workbench and history follow-up (multi-turn continuity)
- Added 30s AbortController timeout to all frontend API calls
- Added try/except and timeout to backend LLM follow-up call

### Enhancements (Phase 3)
- Rewrote investor summary to natural paragraph style (no bold headers)
- Enhanced DeepSeek macro prompt: structured JSON output with 5 dimensions (price/supply/policy/growth/impact)
- Macro section now renders as card grid when structured data available
- Injected DeepSeek macro data into `macro_context` → enables anomaly_scan external rules
- Industry comparison table: added ▲/▼ arrows, color-coded delta, right-aligned numbers, alternating rows
- History follow-up: replaced single-line input with expandable multi-turn chat panel
- Updated 4 legacy tests to match modular code structure
