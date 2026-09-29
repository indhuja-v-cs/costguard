"""Write-through SQLite cache for Azure pricing data."""

from __future__ import annotations

import sqlite3
import time
from decimal import Decimal
from typing import Optional

from .models import PriceRecord

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS pricing_cache (
    sku            TEXT NOT NULL,
    region         TEXT NOT NULL,
    currency       TEXT NOT NULL,
    resource_kind  TEXT NOT NULL,
    hourly_rate    TEXT NOT NULL,
    unit_of_measure TEXT NOT NULL,
    meter_name     TEXT,
    product_name   TEXT,
    cached_at      INTEGER NOT NULL,
    PRIMARY KEY (sku, region, currency, resource_kind)
);
"""

_LOOKUP = """\
SELECT sku, region, currency, resource_kind,
       hourly_rate, unit_of_measure, meter_name, product_name
  FROM pricing_cache
 WHERE sku = ? AND region = ? AND currency = ? AND resource_kind = ?
"""

_UPSERT = """\
INSERT OR REPLACE INTO pricing_cache
    (sku, region, currency, resource_kind,
     hourly_rate, unit_of_measure, meter_name, product_name, cached_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_CLEAR = "DELETE FROM pricing_cache"

_COUNT = "SELECT COUNT(*) FROM pricing_cache"


class PricingCache:
    """Thin write-through cache backed by a local SQLite database."""

    def __init__(self, db_path: str = "pricing_cache.db") -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    # ----- public API -----

    def lookup(
        self, sku: str, region: str, currency: str, resource_kind: str
    ) -> Optional[PriceRecord]:
        """Return a cached ``PriceRecord`` or ``None``."""
        cur = self._conn.execute(_LOOKUP, (sku, region, currency, resource_kind))
        row = cur.fetchone()
        if row is None:
            return None
        return PriceRecord(
            sku=row[0],
            region=row[1],
            currency=row[2],
            resource_kind=row[3],
            hourly_rate=Decimal(row[4]),
            unit_of_measure=row[5],
            meter_name=row[6] or "",
            product_name=row[7] or "",
        )

    def store(self, record: PriceRecord) -> None:
        """Insert or replace a price record."""
        self._conn.execute(
            _UPSERT,
            (
                record.sku,
                record.region,
                record.currency,
                record.resource_kind,
                str(record.hourly_rate),
                record.unit_of_measure,
                record.meter_name,
                record.product_name,
                int(time.time()),
            ),
        )
        self._conn.commit()

    def clear(self) -> None:
        """Delete every cached record."""
        self._conn.execute(_CLEAR)
        self._conn.commit()

    def count(self) -> int:
        """Return the number of cached records."""
        cur = self._conn.execute(_COUNT)
        return cur.fetchone()[0]

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()
