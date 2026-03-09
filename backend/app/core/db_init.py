import logging

from app.config import settings
from app.core.db_adapter import connect, is_postgres_enabled

logger = logging.getLogger("db_init")


def init_all_tables():
    """Initialize all database tables required for the application."""
    logger.info("Initializing all database tables...")

    with connect(settings.DATA_CONTEXT_DB) as conn:
        cursor = conn.cursor()

        if is_postgres_enabled():
            _create_postgres_tables(cursor)
        else:
            _create_sqlite_tables(cursor)

        conn.commit()
        logger.info("All database tables initialized successfully.")


def _create_postgres_tables(cursor):
    """Create tables for PostgreSQL."""

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_context (
            id SERIAL PRIMARY KEY,
            auth0_user_id TEXT NOT NULL,
            context_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            id TEXT PRIMARY KEY,
            auth0_user_id TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL,
            use_cases JSONB DEFAULT '[]',
            goals JSONB DEFAULT '[]',
            onboarding_completed BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendors (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT DEFAULT 'unverified',
            last_verification_date TIMESTAMP,
            next_verification_date TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendor_contracts (
            id TEXT PRIMARY KEY,
            vendor_id TEXT REFERENCES vendors(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            s3_key TEXT,
            audit_status TEXT DEFAULT 'waiting',
            compliance_status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contract_audits (
            id TEXT PRIMARY KEY,
            contract_id TEXT REFERENCES vendor_contracts(id) ON DELETE SET NULL,
            user_id TEXT NOT NULL,
            checklist_type TEXT,
            checklist_name TEXT,
            checklist_items JSONB DEFAULT '[]',
            status TEXT DEFAULT 'pending',
            progress INTEGER DEFAULT 0,
            report_s3_key TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dora_audits (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            company_name TEXT,
            questionnaire_data JSONB,
            documents JSONB DEFAULT '[]',
            status TEXT DEFAULT 'pending',
            progress INTEGER DEFAULT 0,
            results JSONB DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            messages JSONB DEFAULT '[]',
            context_documents JSONB DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendor_qualifications (
            id TEXT PRIMARY KEY,
            vendor_id TEXT,
            vendor_name TEXT,
            user_id TEXT NOT NULL,
            status TEXT DEFAULT 'draft',
            current_step INTEGER DEFAULT 1,
            step_data JSONB DEFAULT '{}',
            is_ict_provider BOOLEAN,
            services_mapping JSONB DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_profiles_auth0 ON user_profiles(auth0_user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendors_user ON vendors(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendor_contracts_user ON vendor_contracts(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendor_contracts_vendor ON vendor_contracts(vendor_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_contract_audits_user ON contract_audits(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_dora_audits_user ON dora_audits(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendor_qualifications_user ON vendor_qualifications(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendor_qualifications_vendor ON vendor_qualifications(vendor_id)")


def _create_sqlite_tables(cursor):
    """Create tables for SQLite."""

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            auth0_user_id TEXT NOT NULL,
            context_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            id TEXT PRIMARY KEY,
            auth0_user_id TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL,
            use_cases TEXT DEFAULT '[]',
            goals TEXT DEFAULT '[]',
            onboarding_completed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendors (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT DEFAULT 'unverified',
            last_verification_date TIMESTAMP,
            next_verification_date TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendor_contracts (
            id TEXT PRIMARY KEY,
            vendor_id TEXT REFERENCES vendors(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            s3_key TEXT,
            audit_status TEXT DEFAULT 'waiting',
            compliance_status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contract_audits (
            id TEXT PRIMARY KEY,
            contract_id TEXT REFERENCES vendor_contracts(id) ON DELETE SET NULL,
            user_id TEXT NOT NULL,
            checklist_type TEXT,
            checklist_name TEXT,
            checklist_items TEXT DEFAULT '[]',
            status TEXT DEFAULT 'pending',
            progress INTEGER DEFAULT 0,
            report_s3_key TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dora_audits (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            company_name TEXT,
            questionnaire_data TEXT,
            documents TEXT DEFAULT '[]',
            status TEXT DEFAULT 'pending',
            progress INTEGER DEFAULT 0,
            results TEXT DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            messages TEXT DEFAULT '[]',
            context_documents TEXT DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendor_qualifications (
            id TEXT PRIMARY KEY,
            vendor_id TEXT,
            vendor_name TEXT,
            user_id TEXT NOT NULL,
            status TEXT DEFAULT 'draft',
            current_step INTEGER DEFAULT 1,
            step_data TEXT DEFAULT '{}',
            is_ict_provider INTEGER,
            services_mapping TEXT DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_profiles_auth0 ON user_profiles(auth0_user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendors_user ON vendors(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendor_contracts_user ON vendor_contracts(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendor_contracts_vendor ON vendor_contracts(vendor_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_contract_audits_user ON contract_audits(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_dora_audits_user ON dora_audits(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendor_qualifications_user ON vendor_qualifications(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendor_qualifications_vendor ON vendor_qualifications(vendor_id)")
