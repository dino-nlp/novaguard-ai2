#!/bin/bash

# Script to completely reset the database: drops all known tables, custom types,
# and functions, then re-applies the schema.
# WARNING: This will delete all data in the specified database.
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
        # Fallback, falls COMPOSE_PROJECT_NAME nicht standardmäßig ist oder -p verwendet wird
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
# The 'projects' table now has a foreign key to 'fullprojectanalysisrequests'
# (last_full_scan_request_id), so 'projects' might need to be dropped before
# 'fullprojectanalysisrequests' if not using CASCADE, OR 'fullprojectanalysisrequests'
# should be dropped with CASCADE if 'projects' still references it.
# Using CASCADE is generally safer for a full reset.

# ENUM type names from your SQLAlchemy models (case-sensitive if defined so,
# but typically lowercased by SQLAlchemy when creating in DB if not quoted in name='...').
# From pr_analysis_request_model.py: name="pr_analysis_status_enum"
# From full_project_analysis_request_model.py: name="full_project_analysis_status_enum"
# From project_model.py: name="project_last_full_scan_status_enum" (create_type=False, so it uses the one from FullProjectAnalysisStatus)

DROP_COMMANDS=$(cat <<EOF
-- Drop tables in an order that respects FKs, or use CASCADE.
-- With CASCADE, the order is less critical, but it's good practice.

-- Foreign keys in 'projects' might reference 'fullprojectanalysisrequests'.
-- 'analysisfindings' references 'pranalysisrequests'.
-- 'pranalysisrequests' references 'projects'.
-- 'projects' references 'users' and 'fullprojectanalysisrequests'.
-- 'fullprojectanalysisrequests' references 'projects'.
-- This creates a cycle if projects.last_full_scan_request_id is FK to fullprojectanalysisrequests.id
-- and fullprojectanalysisrequests.project_id is FK to projects.id.
-- Using CASCADE will handle this.

DROP TABLE IF EXISTS "analysisfindings" CASCADE;
DROP TABLE IF EXISTS "pranalysisrequests" CASCADE;
-- DROP TABLE IF EXISTS "projects" CASCADE; -- Will be dropped due to CASCADE from fullprojectanalysisrequests if cycle exists, or needs to be after fullprojectanalysisrequests
DROP TABLE IF EXISTS "fullprojectanalysisrequests" CASCADE; -- Drop this first if projects refers to it
DROP TABLE IF EXISTS "projects" CASCADE; -- Now drop projects (if not already dropped by cascade)
DROP TABLE IF EXISTS "users" CASCADE;

-- Drop custom ENUM types created by SQLAlchemy.
-- Check your database for the exact names if unsure.
-- SQLAlchemy usually lowercases them.
DROP TYPE IF EXISTS pr_analysis_status_enum CASCADE;
DROP TYPE IF EXISTS full_project_analysis_status_enum CASCADE;
-- The project_last_full_scan_status_enum uses the same type as full_project_analysis_status_enum
-- so dropping full_project_analysis_status_enum should be sufficient.
-- If you defined other ENUMs directly in schema.sql with CREATE TYPE, add them here.

-- Drop functions (if any custom ones besides the trigger)
DROP FUNCTION IF EXISTS trigger_set_timestamp() CASCADE;

-- If you were using Alembic, you might also drop its version table:
-- DROP TABLE IF EXISTS alembic_version CASCADE;

-- Add any other custom sequences, views, or objects here if necessary.
EOF
)

echo "Dropping existing database objects..."
echo "$DROP_COMMANDS" | docker compose exec -T "${DB_SERVICE_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -v ON_ERROR_STOP=1

if [ $? -ne 0 ]; then
    echo "Error occurred while dropping database objects. Check the output above."
    echo "Attempting to apply schema anyway..."
    # You might choose to exit here: exit 1
fi

echo "Applying schema from $SCHEMA_FILE..."
cat "$SCHEMA_FILE" | docker compose exec -T "${DB_SERVICE_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -v ON_ERROR_STOP=1

if [ $? -eq 0 ]; then
    echo "Database '${DB_NAME}' has been successfully reset and schema applied."
else
    echo "Failed to apply schema during reset. Check the output above."
    exit 1
fi

exit 0