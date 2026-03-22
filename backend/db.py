import sqlite3
import threading

_local = threading.local()


def get_conn(db_path: str) -> sqlite3.Connection:
    """Return a per-thread SQLite connection in WAL mode.
    Each ThreadPoolExecutor worker gets its own connection.
    Never share a connection object across threads.
    """
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(db_path, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL;")
        _local.conn.execute("PRAGMA synchronous=NORMAL;")
        _local.conn.execute("PRAGMA foreign_keys=ON;")
    return _local.conn
