#!/bin/sh

if [ -z "$1" ]; then
  echo "no revision message given"
  exit 1
fi

uv run alembic revision --autogenerate -m "$1" \
		--rev-id $(printf "%04d" $(( $(ls migrations/versions/[0-9]*.py | wc -l) + 1 )))
