#!/bin/bash

# Script to completely reset the database: drops all known tables, custom types,
# and functions, then re-applies the schema.
# WARNING: This will delete all data in the specified database.
# Assumes this script is run from the project root (novaguard-ai2/).

SCHEMA_FILE="novaguard-backend/database/schema.sql"
DB_SERVICE_NAME="postgres_db" # Tên service trong docker-compose.yml
DB_USER="novaguard_user"      # User của database
DB_NAME="novaguard_db"        # Tên database

# --- Function to check if docker-compose service is running ---
is_db_service_running() {
    # Versuche, den Container-Namen auf verschiedene Arten zu finden
    local container_name
    container_name=$(docker-compose ps -q "${DB_SERVICE_NAME}" 2>/dev/null)

    if [ -z "$container_name" ]; then
        # Fallback, falls COMPOSE_PROJECT_NAME nicht standardmäßig ist oder -p verwendet wird
        container_name=$(docker ps --filter "name=${DB_SERVICE_NAME}" --format "{{.Names}}" | grep "${DB_SERVICE_NAME}" | head -n 1)
    fi

    if [ -n "$container_name" ] && docker ps -q --filter "name=^/${container_name}$" > /dev/null; then
        return 0 # True, service is running
    else
        return 1 # False, service is not running
    fi
}

# Check if docker-compose is running the db service
if ! is_db_service_running; then
    echo "PostgreSQL service ('${DB_SERVICE_NAME}') is not running or not found."
    echo "Please ensure it's up, e.g., via 'docker-compose up -d ${DB_SERVICE_NAME}'."
    exit 1
fi

if [ ! -f "$SCHEMA_FILE" ]; then
    echo "Schema file not found at $SCHEMA_FILE"
    exit 1
fi

echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
echo "WARNING: This script will completely WIPE all data in the database"
echo "'${DB_NAME}' by dropping all known tables, custom types, and functions,"
echo "then re-applying the schema from '${SCHEMA_FILE}'."
echo "This action is IRREVERSIBLE."
echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
read -p "Are you absolutely sure you want to reset the database '${DB_NAME}'? (Type 'yes' to confirm): " confirmation

if [ "$confirmation" != "yes" ]; then
    echo "Database reset cancelled by the user."
    exit 0
fi

echo "Proceeding with database reset for '${DB_NAME}'..."

# SQL commands to drop all known objects.
# Order is important for objects that depend on each other, unless CASCADE is used.
# Using fully qualified names (e.g., public.tablename) is safer if not default schema.
# PostgreSQL table names are case-sensitive if quoted during creation,
# but typically lowercased if not. Your schema.sql uses mixed case with quotes for tables.
# The ENUM type created by SQLAlchemy will be lowercase.

DROP_COMMANDS=$(cat <<EOF
-- Drop tables in reverse order of dependency, or use CASCADE
-- If a table has a foreign key referencing another, the referenced table should be dropped later,
-- or the referencing table should be dropped with CASCADE.

DROP TABLE IF EXISTS "analysisfindings" CASCADE;
DROP TABLE IF EXISTS "pranalysisrequests" CASCADE;
DROP TABLE IF EXISTS "projects" CASCADE;
DROP TABLE IF EXISTS "users" CASCADE;

-- Drop custom ENUM types created by SQLAlchemy or defined in schema.sql
-- The name of the enum type is 'pr_analysis_status_enum' as defined in your model
-- and `status VARCHAR(20) CHECK (status IN (...))` in schema.sql is a CHECK constraint, not a named ENUM type.
-- If SQLAlchemy's `SQLAlchemyEnum(..., name="pr_analysis_status_enum", create_type=True)`
-- actually created a PostgreSQL ENUM type, you'd drop it like this:
DROP TYPE IF EXISTS pr_analysis_status_enum CASCADE;
-- Add other custom ENUM types here if you have them.

-- Drop functions
DROP FUNCTION IF EXISTS trigger_set_timestamp() CASCADE;

-- If you were using Alembic, you might also drop its version table:
-- DROP TABLE IF EXISTS alembic_version CASCADE;

-- Add any other custom sequences, views, or objects here if necessary.
EOF
)

echo "Dropping existing database objects..."
echo "$DROP_COMMANDS" | docker-compose exec -T "${DB_SERVICE_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -v ON_ERROR_STOP=1

if [ $? -ne 0 ]; then
    echo "Error occurred while dropping database objects. Check the output above."
    echo "Attempting to apply schema anyway..."
    # You might choose to exit here: exit 1
fi

echo "Applying schema from $SCHEMA_FILE..."
cat "$SCHEMA_FILE" | docker-compose exec -T "${DB_SERVICE_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -v ON_ERROR_STOP=1

if [ $? -eq 0 ]; then
    echo "Database '${DB_NAME}' has been successfully reset and schema applied."
else
    echo "Failed to apply schema during reset. Check the output above."
    exit 1
fi

exit 0