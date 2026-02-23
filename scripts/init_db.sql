-- ============================================
-- Database Initialization Script
-- PostGIS extensions and initial setup
-- ============================================

-- Create extensions if they don't exist
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create custom types for enums (if not using ORM)
CREATE TYPE user_role AS ENUM ('USER', 'PANDIT', 'ADMIN');
CREATE TYPE oauth_provider AS ENUM ('GOOGLE', 'FACEBOOK');
CREATE TYPE verification_status AS ENUM ('PENDING', 'VERIFIED', 'REJECTED');
CREATE TYPE booking_status AS ENUM ('PENDING', 'ACCEPTED', 'REJECTED', 'COMPLETED', 'CANCELLED');
CREATE TYPE payment_status AS ENUM ('PENDING', 'COMPLETED', 'FAILED', 'REFUNDED');
CREATE TYPE notification_channel AS ENUM ('FCM', 'SMS', 'EMAIL');

-- Create indexes for common queries (optional, SQLAlchemy handles most)
-- These will be created by the ORM models, but you can add custom ones here if needed

-- Example: Geospatial index on pandit locations
-- CREATE INDEX IF NOT EXISTS idx_pandit_location ON users USING GIST (location)
--   WHERE role = 'PANDIT';

-- Example: Index on booking status for frequent queries
-- CREATE INDEX IF NOT EXISTS idx_booking_status ON bookings(status)
--   WHERE status != 'COMPLETED';

-- Example: Index on payment status for financial queries
-- CREATE INDEX IF NOT EXISTS idx_payment_status ON payments(status)
--   WHERE status IN ('PENDING', 'FAILED');

-- Grant permissions (adjust as needed for your deployment)
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO postgres;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE ON TYPES TO postgres;
