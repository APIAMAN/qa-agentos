#!/usr/bin/env bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT 'CREATE DATABASE qa_agentos_hatchet'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'qa_agentos_hatchet')\gexec
    GRANT ALL PRIVILEGES ON DATABASE qa_agentos_hatchet TO "$POSTGRES_USER";
EOSQL
