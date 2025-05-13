#!/bin/bash

# Script to reset the database: drops relevant tables and functions, then re-applies the schema.
# WARNING: This will delete all data in the specified tables.
# Assumes this script is run from the project root (NOVAGUARD-AI/).

SCHEMA_FILE="novaguard-backend/database/schema.sql"
DB_SERVICE_NAME="postgres_db"
DB_USER="novaguard_user"
DB_NAME="novaguard_db"

# Check if docker-compose is running the db service (similar to init_db.sh)
if ! docker compose ps -q ${DB_SERVICE_NAME} > /dev/null || ! docker ps -q --filter name="^/${COMPOSE_PROJECT_NAME:-novaguard-ai}_${DB_SERVICE_NAME}_1$" > /dev/null && ! docker ps -q --filter name="^/${DB_SERVICE_NAME}$" > /dev/null ; then
    actual_container_name=$(docker compose ps -q ${DB_SERVICE_NAME})
    if [ -z "$actual_container_name" ]; then
         actual_container_name=$(docker ps --filter "name=${DB_SERVICE_NAME}" --format "{{.Names}}" | grep ${DB_SERVICE_NAME})
    fi

    if [ -z "$actual_container_name" ] || ! docker ps -q --filter name="^${actual_container_name}$" > /dev/null ; then
        echo "PostgreSQL service (${DB_SERVICE_NAME}) is not running."
        echo "Please ensure it's up via 'docker-compose up -d ${DB_SERVICE_NAME}'."
        exit 1
    fi
fi


if [ ! -f "$SCHEMA_FILE" ]; then
    echo "Schema file not found at $SCHEMA_FILE"
    exit 1
fi

echo "WARNING: This will delete all data from AnalysisFindings, PRAnalysisRequests, Projects, and Users tables."
read -p "Are you sure you want to reset the database '${DB_NAME}'? (yes/no): " confirmation

if [ "$confirmation" != "yes" ]; then
    echo "Database reset cancelled."
    exit 0
fi

echo "Resetting database '${DB_NAME}'..."

# SQL commands to drop tables and functions
# Order: dependent tables first, or use CASCADE
# Function trigger_set_timestamp is used by Users and Projects, so drop it first or use CASCADE on tables.
DROP_COMMANDS="
DROP FUNCTION IF EXISTS trigger_set_timestamp() CASCADE;
DROP TABLE IF EXISTS \"AnalysisFindings\" CASCADE;
DROP TABLE IF EXISTS \"PRAnalysisRequests\" CASCADE;
DROP TABLE IF EXISTS \"Projects\" CASCADE;
DROP TABLE IF EXISTS \"Users\" CASCADE;
"
# ALembic specific tables if you ever use it
# DROP TABLE IF EXISTS alembic_version CASCADE;

echo "$DROP_COMMANDS" | docker compose exec -T ${DB_SERVICE_NAME} psql -U ${DB_USER} -d ${DB_NAME}

if [ $? -ne 0 ]; then
    echo "Failed to drop existing tables/functions. Continuing to apply schema anyway..."
    # exit 1 # You might choose to exit or continue
fi

echo "Applying schema from $SCHEMA_FILE..."
cat "$SCHEMA_FILE" | docker compose exec -T ${DB_SERVICE_NAME} psql -U ${DB_USER} -d ${DB_NAME}

if [ $? -eq 0 ]; then
    echo "Database reset and schema applied successfully."
else
    echo "Failed to apply schema during reset."
    exit 1
fi

exit 0