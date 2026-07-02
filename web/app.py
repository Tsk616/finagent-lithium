"""
FinAgent-Lithium Web Application.

Minimal entry point: creates the Flask app, registers blueprints,
and provides a health-check endpoint. All route logic lives in
web/routes/, template data building in web/template_data.py.
"""

import sys
from pathlib import Path

# Ensure project root on path so 'nodes.*' imports work everywhere
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from flask import Flask, jsonify
from web.routes import register_blueprints
from web.workflow import KB

# Backward-compatible re-exports: external code (tests)
# imports _build_template_data and shared state from web.app
from web.template_data import build_template_data as _build_template_data  # noqa: F401
from web.shared_state import REPORT_HISTORY as _REPORT_HISTORY  # noqa: F401
from web.shared_state import REPORT_STATES as _REPORT_STATES  # noqa: F401

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32MB max upload

register_blueprints(app)


@app.route("/health")
def health():
    """Health check endpoint for cloud platform monitoring."""
    kb_ok = KB is not None
    return jsonify({
        "status": "ok" if kb_ok else "degraded",
        "kb_loaded": kb_ok,
    })


if __name__ == "__main__":
    import os as _os
    port = int(_os.environ.get("PORT", 5002))
    debug = _os.environ.get("FLASK_DEBUG", "1") == "1"
    print(f"FinAgent-Lithium Web UI")
    print(f"KB loaded: {KB is not None}")
    print(f"Starting at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
