-- Create target databases if they don't exist
-- This script runs at container initialization (only on first start)

DO $$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'data_context') THEN
      PERFORM dblink_exec('dbname=' || current_database(), 'CREATE DATABASE data_context');
   END IF;
END
$$;
