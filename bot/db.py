"""PostgreSQL database layer for DILEVA Base."""

import logging
from datetime import datetime

import asyncpg


logger = logging.getLogger(__name__)

POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 10
COMMAND_TIMEOUT = 10.0


CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id      BIGINT PRIMARY KEY,
    username         TEXT,
    first_name       TEXT,

    status           TEXT NOT NULL DEFAULT 'new',

    source           TEXT,
    added_by         BIGINT,

    claimed_by       BIGINT,
    claimed_at       TIMESTAMPTZ,

    notes            TEXT,

    last_contact_at  TIMESTAMPTZ,
    next_check_at    TIMESTAMPTZ,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen        TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


CREATE_ACTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS actions (
    id           BIGSERIAL PRIMARY KEY,

    telegram_id  BIGINT NOT NULL,
    admin_id     BIGINT,

    action        TEXT NOT NULL,

    old_status   TEXT,
    new_status   TEXT,

    note         TEXT,

    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def create_pool(dsn: str) -> asyncpg.Pool:
    """Open PostgreSQL connection pool and initialize the database."""

    pool = await asyncpg.create_pool(
        dsn,
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
        command_timeout=COMMAND_TIMEOUT,
    )

    async with pool.acquire() as conn:
        await conn.execute(CREATE_USERS_TABLE)
        await conn.execute(CREATE_ACTIONS_TABLE)

    logger.info("PostgreSQL pool ready.")
    return pool


async def upsert_user(
    pool: asyncpg.Pool,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
) -> bool:
    """Insert a user or update their Telegram information."""

    query = """
    INSERT INTO users (
        telegram_id,
        username,
        first_name
    )
    VALUES ($1, $2, $3)

    ON CONFLICT (telegram_id) DO UPDATE
    SET
        username = EXCLUDED.username,
        first_name = EXCLUDED.first_name,
        updated_at = now(),
        last_seen = now()

    RETURNING (xmax = 0) AS is_new;
    """

    is_new = await pool.fetchval(
        query,
        telegram_id,
        username,
        first_name,
    )

    return bool(is_new)


async def get_user(
    pool: asyncpg.Pool,
    telegram_id: int,
):
    """Get one user by Telegram ID."""

    return await pool.fetchrow(
        """
        SELECT *
        FROM users
        WHERE telegram_id = $1;
        """,
        telegram_id,
    )


async def get_user_by_username(
    pool: asyncpg.Pool,
    username: str,
):
    """Find a user by username."""

    username = username.lstrip("@").lower()

    return await pool.fetchrow(
        """
        SELECT *
        FROM users
        WHERE LOWER(username) = $1;
        """,
        username,
    )


async def get_users_by_status(
    pool: asyncpg.Pool,
    status: str,
    limit: int = 50,
):
    """Return users with a specific status."""

    return await pool.fetch(
        """
        SELECT *
        FROM users
        WHERE status = $1
        ORDER BY created_at DESC
        LIMIT $2;
        """,
        status,
        limit,
    )


async def update_status(
    pool: asyncpg.Pool,
    telegram_id: int,
    new_status: str,
    admin_id: int | None = None,
    note: str | None = None,
) -> bool:
    """Change a user's status and save the action in history."""

    user = await get_user(pool, telegram_id)

    if user is None:
        return False

    old_status = user["status"]

    await pool.execute(
        """
        UPDATE users
        SET
            status = $1,
            updated_at = now()
        WHERE telegram_id = $2;
        """,
        new_status,
        telegram_id,
    )

    await pool.execute(
        """
        INSERT INTO actions (
            telegram_id,
            admin_id,
            action,
            old_status,
            new_status,
            note
        )
        VALUES ($1, $2, 'status_change', $3, $4, $5);
        """,
        telegram_id,
        admin_id,
        old_status,
        new_status,
        note,
    )

    return True


async def claim_user(
    pool: asyncpg.Pool,
    telegram_id: int,
    admin_id: int,
) -> bool:
    """Temporarily assign a user to an admin."""

    result = await pool.execute(
        """
        UPDATE users
        SET
            claimed_by = $1,
            claimed_at = now(),
            updated_at = now()
        WHERE telegram_id = $2
          AND (
              claimed_by IS NULL
              OR claimed_by = $1
          );
        """,
        admin_id,
        telegram_id,
    )

    return result.endswith("1")


async def release_user(
    pool: asyncpg.Pool,
    telegram_id: int,
    admin_id: int,
) -> bool:
    """Release a user claimed by an admin."""

    result = await pool.execute(
        """
        UPDATE users
        SET
            claimed_by = NULL,
            claimed_at = NULL,
            updated_at = now()
        WHERE telegram_id = $1
          AND claimed_by = $2;
        """,
        telegram_id,
        admin_id,
    )

    return result.endswith("1")


async def update_contact(
    pool: asyncpg.Pool,
    telegram_id: int,
    next_check_at: datetime | None = None,
) -> bool:
    """Save contact time and optional next check time."""

    result = await pool.execute(
        """
        UPDATE users
        SET
            last_contact_at = now(),
            next_check_at = $1,
            updated_at = now()
        WHERE telegram_id = $2;
        """,
        next_check_at,
        telegram_id,
    )

    return result.endswith("1")


async def add_note(
    pool: asyncpg.Pool,
    telegram_id: int,
    note: str,
) -> bool:
    """Add a note to a user."""

    result = await pool.execute(
        """
        UPDATE users
        SET
            notes = $1,
            updated_at = now()
        WHERE telegram_id = $2;
        """,
        note,
        telegram_id,
    )

    return result.endswith("1")


async def count_users(pool: asyncpg.Pool) -> int:
    """Return total number of users."""

    return int(
        await pool.fetchval(
            "SELECT count(*) FROM users;"
        )
    )


async def count_by_status(
    pool: asyncpg.Pool,
    status: str,
) -> int:
    """Return number of users with a specific status."""

    return int(
        await pool.fetchval(
            """
            SELECT count(*)
            FROM users
            WHERE status = $1;
            """,
            status,
        )
    )


async def close_pool(pool: asyncpg.Pool) -> None:
    """Close PostgreSQL connection pool."""

    await pool.close()
    logger.info("PostgreSQL pool closed.")
