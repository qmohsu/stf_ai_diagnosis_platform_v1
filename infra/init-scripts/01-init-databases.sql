-- ============================================================================
-- STF AI Diagnosis Platform - Database Initialization
-- Author: Li-Ta Hsu
-- Date: January 2026
-- ============================================================================
-- This script creates the application database and user for diagnostic_api
-- Dify database is created automatically by the POSTGRES_DB env var
-- ============================================================================

-- Create application database for diagnostic_api
CREATE DATABASE stf_diagnosis;

-- Create application user (read from env vars at runtime)
-- Note: In production, use separate read-only and write users
DO
$$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_user WHERE usename = 'stf_app_user') THEN
        CREATE USER stf_app_user WITH PASSWORD 'local_dev_password';
    END IF;
END
$$;

-- Grant privileges on application database
GRANT ALL PRIVILEGES ON DATABASE stf_diagnosis TO stf_app_user;
-- Grant schema usage (required for Postgres 15+)
\c stf_diagnosis
GRANT ALL ON SCHEMA public TO stf_app_user;

-- Connect to application database and set up schema
\c stf_diagnosis

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
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session_id (session_id),
    INDEX idx_vehicle_id (vehicle_id),
    INDEX idx_timestamp (timestamp)
);

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
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_vehicle_id_sessions (vehicle_id),
    INDEX idx_technician_id (technician_id),
    INDEX idx_status (status)
);

COMMENT ON TABLE diagnostic_sessions IS 'Diagnostic session tracking';

-- Grant table-specific permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO stf_app_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO stf_app_user;
