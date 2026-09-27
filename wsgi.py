"""WSGI entry point: `flask run` (via FLASK_APP) locally, `gunicorn wsgi:app` in production."""

from dotenv import load_dotenv

load_dotenv()

from app import create_app  # noqa: E402  (load .env before reading config)

app = create_app()
