"""Error handlers: HTML pages for browsers, JSON for /api/ routes."""

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from app.extensions import db


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(HTTPException)
    def handle_http_error(err: HTTPException):
        if request.path.startswith("/api/"):
            return jsonify(error=err.name, message=err.description), err.code
        template = "errors/404.html" if err.code == 404 else "errors/error.html"
        return render_template(template, error=err), err.code

    @app.errorhandler(Exception)
    def handle_unexpected_error(err: Exception):
        db.session.rollback()
        app.logger.exception("Unhandled error on %s", request.path)
        if request.path.startswith("/api/"):
            return jsonify(error="Internal Server Error", message="Something went wrong."), 500
        return render_template("errors/500.html"), 500
