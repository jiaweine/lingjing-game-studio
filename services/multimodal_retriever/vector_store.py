from __future__ import annotations

from pathlib import Path
import sqlite3
import time
from threading import RLock

import numpy as np


class PersistentVectorStore:
    """Tiny SQLite-backed derived vector cache.

    This is deliberately not an authoritative database. Every row is keyed by a stable
    source fingerprint + embedding backend. Delete the DB at any time and workers will
    rebuild it from raw assets. SQLite is sufficient for validating cache/retrieval quality;
    an ANN store can replace it later without changing the worker protocol.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vectors (
                    cache_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (cache_key, backend)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_vectors_updated_at ON vectors(updated_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _normalize(vector: np.ndarray) -> np.ndarray:
        value = np.asarray(vector, dtype=np.float32).reshape(-1)
        return np.ascontiguousarray(value)

    def get(
        self,
        cache_key: str,
        fingerprint: str,
        backend: str,
    ) -> np.ndarray | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT fingerprint, dimension, vector
                FROM vectors
                WHERE cache_key = ? AND backend = ?
                """,
                (cache_key, backend),
            ).fetchone()
        if row is None or str(row[0]) != str(fingerprint):
            return None
        dimension = int(row[1])
        value = np.frombuffer(row[2], dtype=np.float32)
        if dimension <= 0 or value.size != dimension:
            return None
        return value.copy()

    def put(
        self,
        cache_key: str,
        fingerprint: str,
        backend: str,
        vector: np.ndarray,
    ) -> None:
        value = self._normalize(vector)
        if value.size <= 0:
            return
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO vectors (
                    cache_key, fingerprint, backend, dimension, vector, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key, backend) DO UPDATE SET
                    fingerprint = excluded.fingerprint,
                    dimension = excluded.dimension,
                    vector = excluded.vector,
                    updated_at = excluded.updated_at
                """,
                (
                    str(cache_key),
                    str(fingerprint),
                    str(backend),
                    int(value.size),
                    sqlite3.Binary(value.tobytes()),
                    time.time(),
                ),
            )

    def prune(self, *, max_rows: int) -> int:
        """Keep the newest N rows; returns number of deleted derived vectors."""
        max_rows = max(0, int(max_rows))
        with self._lock, self._connect() as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM vectors").fetchone()[0])
            excess = max(0, count - max_rows)
            if excess <= 0:
                return 0
            connection.execute(
                """
                DELETE FROM vectors
                WHERE rowid IN (
                    SELECT rowid FROM vectors ORDER BY updated_at ASC LIMIT ?
                )
                """,
                (excess,),
            )
            return excess

    def count(self) -> int:
        with self._lock, self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM vectors").fetchone()[0])
