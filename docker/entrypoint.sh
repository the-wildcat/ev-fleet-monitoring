#!/bin/sh
# Container start-up: apply database migrations, prepare a fresh deployment, start the server.
set -e

flask db upgrade
flask bootstrap

exec gunicorn --config gunicorn.conf.py wsgi:app
