#!/bin/sh
set -e

# This script runs once on first init of the Postgres data directory
# It creates the target databases used for importing from SQLite

: "${POSTGRES_USER:=esf}"

echo "Creating database: data_context"
createdb -U "$POSTGRES_USER" "data_context"
