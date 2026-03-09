import contextlib
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from app.config import settings
from app.core.db_adapter import connect, execute, is_postgres_enabled
from app.models import GenerationStatus

logger = logging.getLogger("generation_tracker")


def _adapt_query_placeholders(query: str) -> str:
    """Adapt query placeholders based on database backend"""
    if is_postgres_enabled():
        return query.replace("?", "%s")
    return query


class GenerationTracker:
    """Tracks generation progress and status for persistent generation"""

    def __init__(self):
        self.db_path = settings.DATA_CONTEXT_DB
        self._create_table()

    def _create_table(self):
        """Create generation_status table if it doesn't exist"""
        try:
            from app.core.db_adapter import is_postgres_enabled

            if is_postgres_enabled():
                # PostgreSQL-compatible table creation
                create_query = """
                CREATE TABLE IF NOT EXISTS generation_status (
                    generation_id VARCHAR(255) PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL,
                    status VARCHAR(50) NOT NULL,
                    progress INTEGER DEFAULT 0,
                    total_fields INTEGER DEFAULT 0,
                    completed_fields INTEGER DEFAULT 0,
                    current_field TEXT,
                    whitepaper_type VARCHAR(50),
                    results TEXT,
                    error_message TEXT,
                    form TEXT,
                    started_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
                """
            else:
                # SQLite-compatible table creation
                create_query = """
                CREATE TABLE IF NOT EXISTS generation_status (
                    generation_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER DEFAULT 0,
                    total_fields INTEGER DEFAULT 0,
                    completed_fields INTEGER DEFAULT 0,
                    current_field TEXT,
                    whitepaper_type TEXT,
                    results TEXT,
                    error_message TEXT,
                    form TEXT,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """

            with connect(self.db_path) as conn:
                execute(conn, create_query)

                # Try to add form column if missing (for backward compatibility)
                if not is_postgres_enabled():
                    with contextlib.suppress(Exception):
                        execute(conn, "ALTER TABLE generation_status ADD COLUMN form TEXT")
                else:
                    # For PostgreSQL, check if column exists before adding
                    try:
                        cursor = execute(
                            conn,
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_name = 'generation_status' AND column_name = 'form'
                        """,
                        )
                        if not cursor.fetchone():
                            execute(
                                conn,
                                "ALTER TABLE generation_status ADD COLUMN form TEXT",
                            )
                    except Exception:
                        pass

            logger.info("Generation status table created/verified")
        except Exception as e:
            logger.error(f"Error creating generation status table: {e}")

    def create_generation(
        self,
        user_id: str,
        total_fields: int,
        whitepaper_type: str | None = None,
        form: dict[str, Any] | None = None,
    ) -> str:
        """Create a new generation task and return its ID"""
        generation_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()

        try:
            form_json = json.dumps(form) if form else None
            logger.info(f"Creating generation {generation_id} with form data: {form_json[:200] if form_json else 'None'}...")

            with connect(self.db_path) as conn:
                execute(
                    conn,
                    """
                INSERT INTO generation_status
                (generation_id, user_id, status, progress, total_fields, completed_fields, whitepaper_type, form, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        generation_id,
                        user_id,
                        "pending",
                        0,
                        total_fields,
                        0,
                        whitepaper_type,
                        form_json,
                        timestamp,
                        timestamp,
                    ),
                )

            logger.info(f"Created generation {generation_id} for user {user_id} with type {whitepaper_type} and form data saved")
            return generation_id
        except Exception as e:
            logger.error(f"Error creating generation: {e}")
            raise

    def update_generation_status(
        self,
        generation_id: str,
        status: str | None = None,
        progress: int | None = None,
        completed_fields: int | None = None,
        current_field: str | None = None,
        results: dict[str, Any] | None = None,
        error_message: str | None = None,
        form: dict[str, Any] | None = None,
    ):
        """Update generation status"""
        try:
            from app.core.db_adapter import is_postgres_enabled

            # Build update query dynamically based on provided parameters
            update_fields = []
            values = []

            if status is not None:
                update_fields.append("status = %s" if is_postgres_enabled() else "status = ?")
                values.append(status)

            if progress is not None:
                update_fields.append("progress = %s" if is_postgres_enabled() else "progress = ?")
                values.append(progress)

            if completed_fields is not None:
                update_fields.append("completed_fields = %s" if is_postgres_enabled() else "completed_fields = ?")
                values.append(completed_fields)

            if current_field is not None:
                update_fields.append("current_field = %s" if is_postgres_enabled() else "current_field = ?")
                values.append(current_field)

            if results is not None:
                update_fields.append("results = %s" if is_postgres_enabled() else "results = ?")
                values.append(json.dumps(results))

            if error_message is not None:
                update_fields.append("error_message = %s" if is_postgres_enabled() else "error_message = ?")
                values.append(error_message)

            if form is not None:
                update_fields.append("form = %s" if is_postgres_enabled() else "form = ?")
                values.append(json.dumps(form))

            # Always update the timestamp
            if is_postgres_enabled():
                update_fields.append("updated_at = NOW()")
            else:
                update_fields.append("updated_at = ?")
                values.append(datetime.now().isoformat())

            if not update_fields:
                logger.warning(f"No fields to update for generation {generation_id}")
                return

            # Add generation_id for WHERE clause
            values.append(generation_id)

            query = f"""
                UPDATE generation_status
                SET {", ".join(update_fields)}
                WHERE generation_id = {"%s" if is_postgres_enabled() else "?"}
            """

            with connect(self.db_path) as conn:
                execute(conn, query, values)
                logger.debug(f"Updated generation {generation_id}")

        except Exception as e:
            logger.error(f"Error updating generation status: {e}")

            query = f"UPDATE generation_status SET {', '.join(update_fields)} WHERE generation_id = ?"
            execute(conn, query, values)

            logger.info(f"Updated generation {generation_id}: status={status}, progress={progress}")

    def get_generation_status(self, generation_id: str) -> GenerationStatus | None:
        """Get generation status by ID"""
        try:
            query = """
                SELECT generation_id, user_id, status, progress, total_fields, completed_fields,
                       current_field, results, error_message, started_at, updated_at, form
                FROM generation_status WHERE generation_id = ?
                """

            with connect(self.db_path) as conn:
                cur = execute(conn, _adapt_query_placeholders(query), (generation_id,))
                row = cur.fetchone()

            if row:
                # Support dict_row (Postgres) and tuple/Row (SQLite)
                if isinstance(row, dict):
                    results = json.loads(row.get("results")) if row.get("results") else None
                    form = json.loads(row.get("form")) if row.get("form") else None
                    return GenerationStatus(
                        generation_id=row.get("generation_id"),
                        user_id=row.get("user_id"),
                        status=row.get("status"),
                        progress=row.get("progress"),
                        total_fields=row.get("total_fields"),
                        completed_fields=row.get("completed_fields"),
                        current_field=row.get("current_field"),
                        results=results,
                        error_message=row.get("error_message"),
                        form=form,
                        started_at=row.get("started_at"),
                        updated_at=row.get("updated_at"),
                    )
                else:
                    results = json.loads(row[7]) if row[7] else None
                    form = json.loads(row[11]) if row[11] else None
                    return GenerationStatus(
                        generation_id=row[0],
                        user_id=row[1],
                        status=row[2],
                        progress=row[3],
                        total_fields=row[4],
                        completed_fields=row[5],
                        current_field=row[6],
                        results=results,
                        error_message=row[8],
                        form=form,
                        started_at=row[9],
                        updated_at=row[10],
                    )
            return None
        except Exception as e:
            logger.error(f"Error getting generation status: {e}")
            return None

    def get_user_active_generation(self, user_id: str) -> GenerationStatus | None:
        """Get active generation for a user (in_progress or pending)"""
        try:
            query = """
                SELECT generation_id, user_id, status, progress, total_fields, completed_fields,
                       current_field, results, error_message, started_at, updated_at, form
                FROM generation_status
                WHERE user_id = ? AND status IN ('pending', 'in_progress')
                ORDER BY started_at DESC LIMIT 1
                """

            with connect(self.db_path) as conn:
                cur = execute(conn, _adapt_query_placeholders(query), (user_id,))
                row = cur.fetchone()

            if row:
                if isinstance(row, dict):
                    results = json.loads(row.get("results")) if row.get("results") else None
                    form = json.loads(row.get("form")) if row.get("form") else None
                    return GenerationStatus(
                        generation_id=row.get("generation_id"),
                        user_id=row.get("user_id"),
                        status=row.get("status"),
                        progress=row.get("progress"),
                        total_fields=row.get("total_fields"),
                        completed_fields=row.get("completed_fields"),
                        current_field=row.get("current_field"),
                        results=results,
                        error_message=row.get("error_message"),
                        form=form,
                        started_at=row.get("started_at"),
                        updated_at=row.get("updated_at"),
                    )
                else:
                    results = json.loads(row[7]) if row[7] else None
                    form = json.loads(row[11]) if row[11] else None
                    return GenerationStatus(
                        generation_id=row[0],
                        user_id=row[1],
                        status=row[2],
                        progress=row[3],
                        total_fields=row[4],
                        completed_fields=row[5],
                        current_field=row[6],
                        results=results,
                        error_message=row[8],
                        form=form,
                        started_at=row[9],
                        updated_at=row[10],
                    )
            return None
        except Exception as e:
            logger.error(f"Error getting user active generation: {e}")
            return None

    def cleanup_old_generations(self, days: int = 7):
        """Clean up generations older than specified days"""
        try:
            cutoff_date = datetime.now() - timedelta(days=days)
            query = """
                DELETE FROM generation_status
                WHERE started_at < ? AND status IN ('completed', 'failed')
                """

            with connect(self.db_path) as conn:
                cur = execute(conn, _adapt_query_placeholders(query), (cutoff_date.isoformat(),))
                deleted_count = cur.rowcount if hasattr(cur, "rowcount") else 0

            logger.info(f"Cleaned up {deleted_count} old generation records")
        except Exception as e:
            logger.error(f"Error cleaning up old generations: {e}")


# Global instance
generation_tracker = GenerationTracker()
