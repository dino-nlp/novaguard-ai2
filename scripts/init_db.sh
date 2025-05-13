#!/bin/bash

# Script to initialize the database by applying the schema.
# Assumes this script is run from the project root (NOVAGUARD-AI/).

SCHEMA_FILE="novaguard-backend/database/schema.sql"
DB_SERVICE_NAME="postgres_db"
DB_USER="novaguard_user"
DB_NAME="novaguard_db"

# Check if docker-compose is running the db service
if ! docker compose ps -q ${DB_SERVICE_NAME} > /dev/null || ! docker ps -q --filter name="^/${COMPOSE_PROJECT_NAME:-novaguard-ai}_${DB_SERVICE_NAME}_1$" > /dev/null && ! docker ps -q --filter name="^/${DB_SERVICE_NAME}$" > /dev/null ; then
    echo "PostgreSQL service (${DB_SERVICE_NAME}) is not running. Please start it with 'docker-compose up -d ${DB_SERVICE_NAME}'."
    # Try to find container name if COMPOSE_PROJECT_NAME is not set (e.g. running docker compose -p <name> up)
    # A bit more robust check for running container by service name if project name isn't default
    actual_container_name=$(docker compose ps -q ${DB_SERVICE_NAME})
    if [ -z "$actual_container_name" ]; then
         actual_container_name=$(docker ps --filter "name=${DB_SERVICE_NAME}" --format "{{.Names}}" | grep ${DB_SERVICE_NAME})
    fi

    if [ -z "$actual_container_name" ] || ! docker ps -q --filter name="^${actual_container_name}$" > /dev/null ; then
        echo "Could not confirm PostgreSQL service (${DB_SERVICE_NAME}) is running."
        echo "Please ensure it's up via 'docker-compose up -d ${DB_SERVICE_NAME}'."
        exit 1
    fi
fi


if [ ! -f "$SCHEMA_FILE" ]; then
    echo "Schema file not found at $SCHEMA_FILE"
    exit 1
fi

echo "Initializing database '${DB_NAME}'..."
cat "$SCHEMA_FILE" | docker compose exec -T ${DB_SERVICE_NAME} psql -U ${DB_USER} -d ${DB_NAME}

if [ $? -eq 0 ]; then
    echo "Database initialized successfully."
else
    echo "Failed to initialize database."
    exit 1
fi

exit 0