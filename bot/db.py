"""PostgreSQL database layer for DILEVA Base."""

import logging
from datetime import datetime

import asyncpg

logger = logging.getLogger(__name__)

POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 10
COMMAND_TIMEOUT = 10.0


CREATE_CONTACTS_TABLE = """
CREATE TABLE IF NOT EXISTS contacts (
    id BIGSERIAL PRIMARY KEY,

    username TEXT NOT NULL,
    username_normalized TEXT NOT NULL UNIQUE,

    telegram_id BIGINT UNIQUE,
    first_name TEXT,

    status TEXT NOT NULL DEFAULT 'new',

    source TEXT,
    added_by BIGINT,

    claimed_by BIGINT,
    claimed_at TIMESTAMPTZ,

    notes TEXT,

    last_contact_at TIMESTAMPTZ,
    next_check_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


CREATE_ACTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS actions (
    id BIGSERIAL PRIMARY KEY,

    contact_id BIGINT NOT NULL,

    admin_id BIGINT,

    action TEXT NOT NULL,

    old_status TEXT,
    new_status TEXT,

    note TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def create_pool(dsn: str) -> asyncpg.Pool:
    """Open PostgreSQL connection pool and initialize DILEVA tables."""

    pool = await asyncpg.create_pool(
        dsn,
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
        command_timeout=COMMAND_TIMEOUT,
    )

    async with pool.acquire() as conn:
        await conn.execute(CREATE_CONTACTS_TABLE)
        await conn.execute(CREATE_ACTIONS_TABLE)

    logger.info("PostgreSQL pool ready.")
    return pool


async def add_contact(
    pool: asyncpg.Pool,
    username: str,
    added_by: int | None = None,
    source: str | None = None,
):
    """Add a username to DILEVA Base without creating a fake Telegram ID."""

    username = username.strip().lstrip("@")

    if not username:
        return None

    normalized = username.lower()

    return await pool.fetchrow(
        """
        INSERT INTO contacts (
            username,
            username_normalized,
            added_by,
            source
        )
        VALUES ($1, $2, $3, $4)

        ON CONFLICT (username_normalized)
        DO UPDATE SET
            username = EXCLUDED.username,
            updated_at = now()

        RETURNING *;
        """,
        username,
        normalized,
        added_by,
        source,
    )


async def get_contact(
    pool: asyncpg.Pool,
    contact_id: int,
):
    """Get contact by database ID."""

    return await pool.fetchrow(
        """
        SELECT *
        FROM contacts
        WHERE id = $1;
        """,
        contact_id,
    )


async def get_contact_by_username(
    pool: asyncpg.Pool,
    username: str,
):
    """Find contact by username."""

    username = username.strip().lstrip("@").lower()

    return await pool.fetchrow(
        """
        SELECT *
        FROM contacts
        WHERE username_normalized = $1;
        """,
        username,
    )


async def get_contacts_by_status(
    pool: asyncpg.Pool,
    status: str,
    limit: int = 50,
):
    """Return contacts with a specific status."""

    return await pool.fetch(
        """
        SELECT *
        FROM contacts
        WHERE status = $1
        ORDER BY created_at DESC
        LIMIT $2;
        """,
        status,
        limit,
    )


async def update_status(
    pool: asyncpg.Pool,
    contact_id: int,
    new_status: str,
    admin_id: int | None = None,
    note: str | None = None,
) -> bool:
    """Change contact status and save the action."""

    contact = await get_contact(pool, contact_id)

    if contact is None:
        return False

    old_status = contact["status"]

    await pool.execute(
        """
        UPDATE contacts
        SET
            status = $1,
            updated_at = now()
        WHERE id = $2;
        """,
        new_status,
        contact_id,
    )

    await pool.execute(
        """
        INSERT INTO actions (
            contact_id,
            admin_id,
            action,
            old_status,
            new_status,
            note
        )
        VALUES ($1, $2, 'status_change', $3, $4, $5);
        """,
        contact_id,
        admin_id,
        old_status,
        new_status,
        note,
    )

    return True


async def claim_contact(
    pool: asyncpg.Pool,
    contact_id: int,
    admin_id: int,
) -> bool:
    """Temporarily assign a contact to an admin."""

    result = await pool.execute(
        """
        UPDATE contacts
        SET
            claimed_by = $1,
            claimed_at = now(),
            updated_at = now()
        WHERE id = $2
          AND (
              claimed_by IS NULL
              OR claimed_by = $1
          );
        """,
        admin_id,
        contact_id,
    )

    return result.endswith("1")


async def release_contact(
    pool: asyncpg.Pool,
    contact_id: int,
    admin_id: int,
) -> bool:
    """Release a contact claimed by an admin."""

    result = await pool.execute(
        """
        UPDATE contacts
        SET
            claimed_by = NULL,
            claimed_at = NULL,
            updated_at = now()
        WHERE id = $1
          AND claimed_by = $2;
        """,
        contact_id,
        admin_id,
    )

    return result.endswith("1")


async def update_contact_time(
    pool: asyncpg.Pool,
    contact_id: int,
    next_check_at: datetime | None = None,
) -> bool:
    """Save contact time and optional next check time."""

    result = await pool.execute(
        """
        UPDATE contacts
        SET
            last_contact_at = now(),
            next_check_at = $1,
            updated_at = now()
        WHERE id = $2;
        """,
        next_check_at,
        contact_id,
    )

    return result.endswith("1")


async def add_note(
    pool: asyncpg.Pool,
    contact_id: int,
    note: str,
) -> bool:
    """Save a note for a contact."""

    result = await pool.execute(
        """
        UPDATE contacts
        SET
            notes = $1,
            updated_at = now()
        WHERE id = $2;
        """,
        note,
        contact_id,
    )

    return result.endswith("1")


async def count_contacts(pool: asyncpg.Pool) -> int:
    """Return total number of contacts."""

    return int(
        await pool.fetchval(
            """
            SELECT count(*)
            FROM contacts;
            """
        )
    )


async def count_by_status(
    pool: asyncpg.Pool,
    status: str,
) -> int:
    """Return number of contacts with a specific status."""

    return int(
        await pool.fetchval(
            """
            SELECT count(*)
            FROM contacts
            WHERE status = $1;
            """,
            status,
        )
    )


async def close_pool(pool: asyncpg.Pool) -> None:
    """Close PostgreSQL connection pool."""

    await pool.close()
    logger.info("PostgreSQL pool closed.")
