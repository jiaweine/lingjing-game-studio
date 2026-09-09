from __future__ import annotations

from pathlib import Path
import sqlite3
import time
from threading import RLock
from typing import Any

import numpy as np


class PersistentVectorStore:
    """SQLite-backed, fully rebuildable retrieval index.

    Raw project assets remain authoritative. This store keeps only derived vectors, source
    locators, indexing-completion markers, and short-lived build leases. Deleting the DB
    must never lose project information; workers can recreate every row from raw files.
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
                """
                CREATE TABLE IF NOT EXISTS units (
                    cache_key TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    modality TEXT NOT NULL,
                    start REAL,
                    end REAL,
                    char_start INTEGER,
                    char_end INTEGER,
                    excerpt TEXT,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (cache_key, backend)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS index_meta (
                    meta_key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS build_leases (
                    cache_key TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (cache_key, backend)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_vectors_updated_at ON vectors(updated_at)"
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_units_source
                ON units(source_key, source_fingerprint, backend, modality)
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_build_leases_expiry ON build_leases(expires_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _normalize(vector: np.ndarray) -> np.ndarray:
        value = np.asarray(vector, dtype=np.float32).reshape(-1)
        return np.ascontiguousarray(value)

    @staticmethod
    def _decode_vector(dimension: int, blob: bytes) -> np.ndarray | None:
        value = np.frombuffer(blob, dtype=np.float32)
        if int(dimension) <= 0 or value.size != int(dimension):
            return None
        return value.copy()

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
        return self._decode_vector(int(row[1]), row[2])

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

    def put_unit(
        self,
        *,
        cache_key: str,
        backend: str,
        source_key: str,
        source_fingerprint: str,
        modality: str,
        start: float | None = None,
        end: float | None = None,
        char_start: int | None = None,
        char_end: int | None = None,
        excerpt: str | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO units (
                    cache_key, backend, source_key, source_fingerprint, modality,
                    start, end, char_start, char_end, excerpt, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key, backend) DO UPDATE SET
                    source_key = excluded.source_key,
                    source_fingerprint = excluded.source_fingerprint,
                    modality = excluded.modality,
                    start = excluded.start,
                    end = excluded.end,
                    char_start = excluded.char_start,
                    char_end = excluded.char_end,
                    excerpt = excluded.excerpt,
                    updated_at = excluded.updated_at
                """,
                (
                    str(cache_key),
                    str(backend),
                    str(source_key),
                    str(source_fingerprint),
                    str(modality),
                    start,
                    end,
                    char_start,
                    char_end,
                    excerpt,
                    time.time(),
                ),
            )

    def list_source_vectors(
        self,
        *,
        source_key: str,
        source_fingerprint: str,
        backend: str,
        modality: str,
    ) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    u.cache_key, u.start, u.end, u.char_start, u.char_end, u.excerpt,
                    v.dimension, v.vector
                FROM units AS u
                JOIN vectors AS v
                  ON v.cache_key = u.cache_key AND v.backend = u.backend
                WHERE u.source_key = ?
                  AND u.source_fingerprint = ?
                  AND u.backend = ?
                  AND u.modality = ?
                ORDER BY COALESCE(u.char_start, 0), COALESCE(u.start, 0)
                """,
                (
                    str(source_key),
                    str(source_fingerprint),
                    str(backend),
                    str(modality),
                ),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            vector = self._decode_vector(int(row[6]), row[7])
            if vector is None:
                continue
            out.append(
                {
                    "cache_key": str(row[0]),
                    "start": row[1],
                    "end": row[2],
                    "char_start": row[3],
                    "char_end": row[4],
                    "excerpt": row[5],
                    "vector": vector,
                }
            )
        return out

    def get_meta(self, meta_key: str) -> str | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM index_meta WHERE meta_key = ?",
                (str(meta_key),),
            ).fetchone()
        return str(row[0]) if row is not None else None

    def set_meta(self, meta_key: str, value: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO index_meta(meta_key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(meta_key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (str(meta_key), str(value), time.time()),
            )

    def try_claim_build(
        self,
        *,
        cache_key: str,
        backend: str,
        owner: str,
        lease_seconds: float = 300.0,
    ) -> bool:
        """Claim derived-index work across processes without blocking online requests.

        Expired leases are stealable, so worker crashes cannot permanently wedge an asset.
        Callers that fail to acquire should use any partial index already present or fall
        back to deterministic evidence rather than duplicating expensive GPU work.
        """
        now = time.time()
        expires = now + max(0.05, float(lease_seconds))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT owner, expires_at
                FROM build_leases
                WHERE cache_key = ? AND backend = ?
                """,
                (str(cache_key), str(backend)),
            ).fetchone()
            if row is not None and float(row[1]) > now and str(row[0]) != str(owner):
                connection.rollback()
                return False
            connection.execute(
                """
                INSERT INTO build_leases(cache_key, backend, owner, expires_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key, backend) DO UPDATE SET
                    owner = excluded.owner,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (str(cache_key), str(backend), str(owner), expires, now),
            )
            connection.commit()
            return True

    def refresh_build_lease(
        self,
        *,
        cache_key: str,
        backend: str,
        owner: str,
        lease_seconds: float = 300.0,
    ) -> bool:
        now = time.time()
        expires = now + max(0.05, float(lease_seconds))
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE build_leases
                SET expires_at = ?, updated_at = ?
                WHERE cache_key = ? AND backend = ? AND owner = ?
                """,
                (expires, now, str(cache_key), str(backend), str(owner)),
            )
            return int(cursor.rowcount or 0) > 0

    def release_build(
        self,
        *,
        cache_key: str,
        backend: str,
        owner: str,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                DELETE FROM build_leases
                WHERE cache_key = ? AND backend = ? AND owner = ?
                """,
                (str(cache_key), str(backend), str(owner)),
            )

    def prune(self, *, max_rows: int) -> int:
        """Keep newest vectors and clean orphaned metadata/expired build leases."""
        max_rows = max(0, int(max_rows))
        with self._lock, self._connect() as connection:
            count = int(
                connection.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
            )
            excess = max(0, count - max_rows)
            if excess > 0:
                connection.execute(
                    """
                    DELETE FROM vectors
                    WHERE rowid IN (
                        SELECT rowid FROM vectors ORDER BY updated_at ASC LIMIT ?
                    )
                    """,
                    (excess,),
                )
                connection.execute(
                    """
                    DELETE FROM units
                    WHERE NOT EXISTS (
                        SELECT 1 FROM vectors AS v
                        WHERE v.cache_key = units.cache_key
                          AND v.backend = units.backend
                    )
                    """
                )
            connection.execute(
                "DELETE FROM build_leases WHERE expires_at <= ?",
                (time.time(),),
            )
            return excess

    def count(self) -> int:
        with self._lock, self._connect() as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
            )
