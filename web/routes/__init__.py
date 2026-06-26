"""
Flask Blueprint registration for FinAgent-Lithium.

Design: Each route module is a Blueprint handling one functional area.
This keeps individual files focused and testable.
"""

from flask import Flask


def register_blueprints(app: Flask) -> None:
    from web.routes.analysis import bp as analysis_bp
    from web.routes.followup import bp as followup_bp
    from web.routes.peers import bp as peers_bp
    from web.routes.history import bp as history_bp

    app.register_blueprint(analysis_bp)
    app.register_blueprint(followup_bp)
    app.register_blueprint(peers_bp)
    app.register_blueprint(history_bp)
