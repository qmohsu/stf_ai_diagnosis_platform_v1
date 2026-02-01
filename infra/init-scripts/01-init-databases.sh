#!/bin/bash
# ============================================================================
# STF AI Diagnosis Platform - Database Initialization
# Author: Li-Ta Hsu
# Date: January 2026
# ============================================================================
# This script creates the application database and user for diagnostic_api
# Dify database is created automatically by the POSTGRES_DB env var
# ============================================================================
set -e

: "${APP_DB_PASSWORD:?APP_DB_PASSWORD environment variable must be set}"

# Create application database (guard against re-runs)
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
    SELECT 'CREATE DATABASE stf_diagnosis'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'stf_diagnosis')\gexec
EOSQL

# Create application user with password from environment
# Uses psql variable binding (-v) to avoid SQL injection via password value
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_password="$APP_DB_PASSWORD" <<-'EOSQL'
    SELECT format('CREATE USER stf_app_user WITH PASSWORD %L', :'app_password')
    WHERE NOT EXISTS (SELECT FROM pg_user WHERE usename = 'stf_app_user')\gexec

    -- Grant privileges on application database
    GRANT ALL PRIVILEGES ON DATABASE stf_diagnosis TO stf_app_user;
EOSQL

# Connect to application database and set up schema
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "stf_diagnosis" <<-'EOSQL'
    -- Grant schema usage (required for Postgres 15+)
    GRANT ALL ON SCHEMA public TO stf_app_user;

    -- Create extensions if needed
    CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

    -- Set default privileges for future tables
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO stf_app_user;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO stf_app_user;

    -- Create initial schema (placeholder for Phase 1)
    CREATE TABLE IF NOT EXISTS interaction_logs (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        session_id VARCHAR(255) NOT NULL,
        vehicle_id VARCHAR(255),
        user_input TEXT,
        retrieved_chunks JSONB,
        tool_outputs JSONB,
        final_response JSONB,
        timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_session_id ON interaction_logs (session_id);
    CREATE INDEX IF NOT EXISTS idx_vehicle_id ON interaction_logs (vehicle_id);
    CREATE INDEX IF NOT EXISTS idx_timestamp ON interaction_logs (timestamp);

    COMMENT ON TABLE interaction_logs IS 'Persistent logs for Phase 1.5 training data collection';

    -- Create diagnostic sessions table
    CREATE TABLE IF NOT EXISTS diagnostic_sessions (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        vehicle_id VARCHAR(255) NOT NULL,
        technician_id VARCHAR(255),
        start_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        end_time TIMESTAMP WITH TIME ZONE,
        status VARCHAR(50) DEFAULT 'active',
        metadata JSONB,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_vehicle_id_sessions ON diagnostic_sessions (vehicle_id);
    CREATE INDEX IF NOT EXISTS idx_technician_id ON diagnostic_sessions (technician_id);
    CREATE INDEX IF NOT EXISTS idx_status ON diagnostic_sessions (status);

    COMMENT ON TABLE diagnostic_sessions IS 'Diagnostic session tracking';

    -- Grant table-specific permissions
    GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO stf_app_user;
    GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO stf_app_user;
EOSQL
