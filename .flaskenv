# Non-secret Flask CLI settings (read automatically by `flask`). Secrets go in .env.
FLASK_APP=wsgi.py
# Auto-reload + debugger for local development only; Docker/Render use gunicorn, which ignores this.
FLASK_DEBUG=1
