"""
================================================================================
FnOQuantAI: Database Manager
================================================================================
Manages project-owned PostgreSQL tables (fo_*) and enforces strict isolation:
- Idempotent schema initialization via database/schema.sql.
- Strict protection barrier: Write mutations on CommonModule tables are blocked.
- Dedicated query and execution wrappers for FnOQuantAI operations.
================================================================================
"""

import os
import sys
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Union, Sequence, Mapping

# Resolve CommonModule
COMMON_MODULE_DIR = r"D:\SM_Server\CommonModule"
if COMMON_MODULE_DIR not in sys.path:
    sys.path.insert(0, COMMON_MODULE_DIR)

from script import postgres_connection

def get_primary_connection():
    """Returns primary write connection to PostgreSQL."""
    return postgres_connection.get_write_connection()

def get_read_connection():
    """Returns read replica connection to PostgreSQL."""
    return postgres_connection.get_read_connection()


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_SQL_PATH = os.path.join(CURRENT_DIR, "schema.sql")

log = logging.getLogger("FnOQuantAI.Database")

# Tables strictly belonging to CommonModule that must never be modified by this tool
FORBIDDEN_MUTATION_TABLES = {
    "market_commodity_snapshots",
    "market_equity_snapshots",
    "market_index_snapshots"
}


def assert_safe_write_statement(sql_statement: str) -> None:
    """
    Guards CommonModule tables against accidental mutation.
    Raises PermissionError if a write statement targets any protected table.
    """
    sql_upper = sql_statement.upper()
    for forbidden in FORBIDDEN_MUTATION_TABLES:
        for verb in ("INSERT INTO", "UPDATE", "DELETE FROM", "DROP TABLE", "ALTER TABLE", "TRUNCATE"):
            target_pattern = f"{verb} {forbidden.upper()}"
            if target_pattern in sql_upper:
                raise PermissionError(
                    f"CRITICAL SAFETY VIOLATION: FnOQuantAI is prohibited from executing '{verb}' "
                    f"against CommonModule table '{forbidden}'."
                )


def init_schema(schema_file: str = SCHEMA_SQL_PATH) -> bool:
    """
    Applies schema.sql to the database to create all required fo_* tables.
    Safe and idempotent (uses CREATE TABLE IF NOT EXISTS).
    """
    if not os.path.exists(schema_file):
        raise FileNotFoundError(f"Schema file not found at: {schema_file}")

    with open(schema_file, "r", encoding="utf-8") as f:
        sql_content = f.read().lstrip("\ufeff")

    log.info("Initializing FnOQuantAI database schema...")
    try:
        postgres_connection.execute_write(sql_content)
        log.info("FnOQuantAI schema initialized successfully (all fo_* tables verified).")
        return True
    except Exception as e:
        log.error(f"Failed to initialize FnOQuantAI schema: {e}")
        raise


def execute_fo_write(
    sql_write: str,
    params: Optional[Union[Sequence, Mapping]] = None
) -> int:
    """
    Executes an INSERT, UPDATE, or DELETE on FnOQuantAI-owned tables.
    Enforces the safety assertion before dispatching.
    """
    assert_safe_write_statement(sql_write)
    return postgres_connection.execute_write(sql_write, params)


def execute_fo_batch(
    sql_statement: str,
    rows: List[Union[Sequence, Mapping]]
) -> int:
    """
    Executes a bulk batch write on FnOQuantAI-owned tables.
    Enforces the safety assertion before dispatching.
    """
    assert_safe_write_statement(sql_statement)
    return postgres_connection.execute_many(sql_statement, rows)


def execute_fo_query(
    sql_query: str,
    params: Optional[Union[Sequence, Mapping]] = None,
    use_primary: bool = True
) -> List[Dict[str, Any]]:
    """
    Executes a SELECT query on FnOQuantAI tables.
    Defaults to primary connection (use_primary=True) for immediate read-your-own-writes consistency.
    """
    if use_primary:
        return postgres_connection.execute_query(sql_query, params, conn=postgres_connection.get_write_connection())
    return postgres_connection.execute_query(sql_query, params)


def record_worker_execution(
    worker_name: str,
    cycle_id: Optional[str] = None,
    started_at: Optional[Any] = None,
    completed_at: Optional[Any] = None,
    duration_seconds: Optional[float] = None,
    records_examined: int = 0,
    new_data_accepted: int = 0,
    signals_generated: int = 0,
    active_calls_monitored: int = 0,
    exit_events_triggered: int = 0,
    status: str = "SUCCESS",
    error_message: Optional[str] = None
) -> None:
    """
    Records a cycle execution log in fo_worker_executions for monitoring & health auditing.
    """
    sql = """
        INSERT INTO fo_worker_executions (
            worker_name, cycle_id, started_at, completed_at, duration_seconds,
            records_examined, new_data_accepted, signals_generated,
            active_calls_monitored, exit_events_triggered, status, error_message
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        );
    """
    try:
        execute_fo_write(sql, (
            worker_name, cycle_id, started_at, completed_at, duration_seconds,
            records_examined, new_data_accepted, signals_generated,
            active_calls_monitored, exit_events_triggered, status, error_message
        ))
    except Exception as e:
        log.error(f"Failed to record worker execution audit log for {worker_name}: {e}")
