#!/bin/bash

# Script to initialize the database by applying the schema.
# Assumes this script is run from the project root (novaguard-ai2/).

SCHEMA_FILE="novaguard-backend/database/schema.sql"
DB_SERVICE_NAME="postgres_db" # Tên service trong docker compose.yml
DB_USER="novaguard_user"      # User của database
DB_NAME="novaguard_db"        # Tên database

# --- Function to check if docker compose service is running ---
is_db_service_running() {
    local container_name
    container_name=$(docker compose ps -q "${DB_SERVICE_NAME}" 2>/dev/null)

    if [ -z "$container_name" ]; then
        # Fallback if COMPOSE_PROJECT_NAME is not default or -p is used
        container_name=$(docker ps --filter "name=${DB_SERVICE_NAME}" --format "{{.Names}}" | grep "${DB_SERVICE_NAME}" | head -n 1)
    fi

    if [ -n "$container_name" ] && docker ps -q --filter "name=^/${container_name}$" > /dev/null; then
        return 0 # True, service is running
    else
        return 1 # False, service is not running
    fi
}

# Check if docker compose is running the db service
if ! is_db_service_running; then
    echo "PostgreSQL service ('${DB_SERVICE_NAME}') is not running or not found."
    echo "Please ensure it's up, e.g., via 'docker compose up -d ${DB_SERVICE_NAME}'."
    exit 1
fi

if [ ! -f "$SCHEMA_FILE" ]; then
    echo "Schema file not found at $SCHEMA_FILE"
    echo "Please ensure your 'novaguard-backend/database/schema.sql' is up to date with all tables and types."
    exit 1
fi

echo "Initializing database '${DB_NAME}' by applying schema from '${SCHEMA_FILE}'..."
echo "Ensuring schema.sql includes 'fullprojectanalysisrequests' table and updated 'projects' table definitions."

cat "$SCHEMA_FILE" | docker compose exec -T "${DB_SERVICE_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -v ON_ERROR_STOP=1

if [ $? -eq 0 ]; then
    echo "Database '${DB_NAME}' initialized successfully with the schema from '${SCHEMA_FILE}'."
else
    echo "Failed to initialize database '${DB_NAME}'. Check the output above and ensure '${SCHEMA_FILE}' is correct."
    exit 1
fi

exit 0