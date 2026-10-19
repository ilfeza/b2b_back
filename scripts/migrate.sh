#!/bin/sh
set -eu
service_name=${1:-b2b}
case "$service_name" in
  b2b|goods|publications) ;;
  *) echo 'Usage: sh scripts/migrate.sh b2b|goods|publications' >&2; exit 2 ;;
esac
exec alembic -c "alembic-$service_name.ini" upgrade head
