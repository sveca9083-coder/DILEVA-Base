"""PostgreSQL database layer for DILEVA Base."""

import logging
from datetime import datetime, timedelta

import asyncpg

logger = logging.getLogger(__name__)

POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 10
COMMAND_TIMEOUT = 10.0

STATUS_NEW = "new"
STATUS_NO_REPLY = "no_reply"
STATUS_REFUSED = "refused"
STATUS_UNDER_16 = "under_16"
STATUS_JOINED = "joined"
STATUS_LEFT = "left"
STATUS_NOT_WORKING = "not_working"


CREATE_CONTACTS_TABLE = """
CREATE TABLE IF NOT EXISTS contacts (
    id BIGSERIAL PRIMARY KEY,

    username TEXT NOT NULL,
    username_normalized TEXT NOT NULL UNIQUE,

    telegram_id BIGINT UNIQUE,
    first_name TEXT,

    status TEXT NOT NULL DEFAULT 'new',

    age INTEGER,

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


ALTER_CONTACTS_TABLE = """
ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS age INTEGER;

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS telegram_id BIGINT;

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS first_name TEXT;

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'new';

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS source TEXT;

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS added_by BIGINT;

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS claimed_by BIGINT;

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS notes TEXT;

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS last_contact_at TIMESTAMPTZ;

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS next_check_at TIMESTAMPTZ;

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ NOT NULL DEFAULT now();
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


ALTER_ACTIONS_TABLE = """
ALTER TABLE actions
    ADD COLUMN IF NOT EXISTS contact_id BIGINT;

ALTER TABLE actions
    ADD COLUMN IF NOT EXISTS admin_id BIGINT;

ALTER TABLE actions
    ADD COLUMN IF NOT EXISTS action TEXT;

ALTER TABLE actions
    ADD COLUMN IF NOT EXISTS old_status TEXT;

ALTER TABLE actions
    ADD COLUMN IF NOT EXISTS new_status TEXT;

ALTER TABLE actions
    ADD COLUMN IF NOT EXISTS note TEXT;

ALTER TABLE actions
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
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
        await conn.execute(ALTER_CONTACTS_TABLE)

        await conn.execute(CREATE_ACTIONS_TABLE)
        await conn.execute(ALTER_ACTIONS_TABLE)

    logger.info("PostgreSQL pool ready.")

    return pool


# =========================
# CONTACTS
# =========================

async def add_contact(
    pool: asyncpg.Pool,
    username: str,
    added_by: int | None = None,
    source: str | None = None,
):
    """Add a username to DILEVA Base."""

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


async def get_contact_by_telegram_id(
    pool: asyncpg.Pool,
    telegram_id: int,
):
    """Find contact by Telegram ID."""

    return await pool.fetchrow(
        """
        SELECT *
        FROM contacts
        WHERE telegram_id = $1;
        """,
        telegram_id,
    )


async def update_telegram_user(
    pool: asyncpg.Pool,
    contact_id: int,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
):
    """Save Telegram information for a contact."""

    normalized_username = None

    if username:
        normalized_username = username.strip().lstrip("@").lower()

    return await pool.fetchrow(
        """
        UPDATE contacts
        SET
            telegram_id = $1,
            username = COALESCE(NULLIF($2, ''), username),
            username_normalized = COALESCE(
                $3,
                username_normalized
            ),
            first_name = COALESCE($4, first_name),
            last_seen = now(),
            updated_at = now()
        WHERE id = $5
        RETURNING *;
        """,
        telegram_id,
        username,
        normalized_username,
        first_name,
        contact_id,
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


# =========================
# ADMIN DISPLAY NAME
# =========================

async def get_admin_display_name(
    pool: asyncpg.Pool,
    admin_id: int | None,
) -> str:
    """Return a readable admin name."""

    if admin_id is None:
        return "неизвестный админ"

    user = await pool.fetchrow(
        """
        SELECT username, first_name
        FROM users
        WHERE telegram_id = $1;
        """,
        admin_id,
    )

    if user is None:
        return f"ID {admin_id}"

    if user["username"]:
        return f"@{user['username']}"

    if user["first_name"]:
        return user["first_name"]

    return f"ID {admin_id}"


# =========================
# STATUS
# =========================

async def update_status(
    pool: asyncpg.Pool,
    contact_id: int,
    new_status: str,
    admin_id: int | None = None,
    note: str | None = None,
) -> bool:
    """Change contact status and save the action."""

    contact = await get_contact(
        pool,
        contact_id,
    )

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
        VALUES (
            $1,
            $2,
            'status_change',
            $3,
            $4,
            $5
        );
        """,
        contact_id,
        admin_id,
        old_status,
        new_status,
        note,
    )

    return True


async def set_no_reply(
    pool: asyncpg.Pool,
    contact_id: int,
    admin_id: int | None = None,
) -> bool:
    """Move contact to no-reply and start 48-hour timer."""

    contact = await get_contact(
        pool,
        contact_id,
    )

    if contact is None:
        return False

    old_status = contact["status"]

    next_check = datetime.now().astimezone() + timedelta(
        hours=48
    )

    await pool.execute(
        """
        UPDATE contacts
        SET
            status = $1,
            last_contact_at = now(),
            next_check_at = $2,
            claimed_by = NULL,
            claimed_at = NULL,
            updated_at = now()
        WHERE id = $3;
        """,
        STATUS_NO_REPLY,
        next_check,
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
        VALUES (
            $1,
            $2,
            'no_reply_48h',
            $3,
            $4,
            '48-hour timer started'
        );
        """,
        contact_id,
        admin_id,
        old_status,
        STATUS_NO_REPLY,
    )

    return True


async def return_expired_no_reply(
    pool: asyncpg.Pool,
) -> int:
    """Return expired no-reply contacts to the new queue."""

    rows = await pool.fetch(
        """
        UPDATE contacts
        SET
            status = $1,
            next_check_at = NULL,
            last_contact_at = NULL,
            claimed_by = NULL,
            claimed_at = NULL,
            updated_at = now()
        WHERE status = $2
          AND next_check_at IS NOT NULL
          AND next_check_at <= now()
        RETURNING id;
        """,
        STATUS_NEW,
        STATUS_NO_REPLY,
    )

    for row in rows:
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
            VALUES (
                $1,
                NULL,
                'timer_expired',
                $2,
                $3,
                '48-hour timer expired'
            );
            """,
            row["id"],
            STATUS_NO_REPLY,
            STATUS_NEW,
        )

    return len(rows)


# =========================
# NOT WORKING
# =========================

async def set_not_working(
    pool: asyncpg.Pool,
    contact_id: int,
    admin_id: int | None = None,
) -> bool:
    """Move contact to not-working status."""

    contact = await get_contact(
        pool,
        contact_id,
    )

    if contact is None:
        return False

    old_status = contact["status"]

    await pool.execute(
        """
        UPDATE contacts
        SET
            status = $1,
            claimed_by = NULL,
            claimed_at = NULL,
            next_check_at = NULL,
            updated_at = now()
        WHERE id = $2;
        """,
        STATUS_NOT_WORKING,
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
        VALUES (
            $1,
            $2,
            'status_change',
            $3,
            $4,
            'Marked as not working'
        );
        """,
        contact_id,
        admin_id,
        old_status,
        STATUS_NOT_WORKING,
    )

    return True


async def return_to_new(
    pool: asyncpg.Pool,
    contact_id: int,
    admin_id: int | None = None,
) -> bool:
    """Return a not-working contact to new."""

    contact = await get_contact(
        pool,
        contact_id,
    )

    if contact is None:
        return False

    old_status = contact["status"]

    if old_status != STATUS_NOT_WORKING:
        return False

    await pool.execute(
        """
        UPDATE contacts
        SET
            status = $1,
            claimed_by = NULL,
            claimed_at = NULL,
            next_check_at = NULL,
            updated_at = now()
        WHERE id = $2;
        """,
        STATUS_NEW,
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
        VALUES (
            $1,
            $2,
            'status_change',
            $3,
            $4,
            'Returned to new queue'
        );
        """,
        contact_id,
        admin_id,
        old_status,
        STATUS_NEW,
    )

    return True


# =========================
# AGE
# =========================

async def set_age(
    pool: asyncpg.Pool,
    contact_id: int,
    age: int,
    admin_id: int | None = None,
) -> bool:
    """Save contact age."""

    if age < 0 or age > 120:
        return False

    result = await pool.execute(
        """
        UPDATE contacts
        SET
            age = $1,
            updated_at = now()
        WHERE id = $2;
        """,
        age,
        contact_id,
    )

    if result.endswith("1"):
        await pool.execute(
            """
            INSERT INTO actions (
                contact_id,
                admin_id,
                action,
                note
            )
            VALUES (
                $1,
                $2,
                'age_set',
                $3
            );
            """,
            contact_id,
            admin_id,
            f"Age: {age}",
        )

        return True

    return False


async def set_age_and_status(
    pool: asyncpg.Pool,
    contact_id: int,
    age: int,
    status: str,
    admin_id: int | None = None,
) -> bool:
    """Save age and status together."""

    if age < 0 or age > 120:
        return False

    contact = await get_contact(
        pool,
        contact_id,
    )

    if contact is None:
        return False

    old_status = contact["status"]

    next_check = None

    if status == STATUS_NO_REPLY:
        next_check = datetime.now().astimezone() + timedelta(
            hours=48
        )

    await pool.execute(
        """
        UPDATE contacts
        SET
            age = $1,
            status = $2,
            next_check_at = $3,
            last_contact_at = CASE
                WHEN $2 = 'no_reply'
                THEN now()
                ELSE last_contact_at
            END,
            claimed_by = NULL,
            claimed_at = NULL,
            updated_at = now()
        WHERE id = $4;
        """,
        age,
        status,
        next_check,
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
        VALUES (
            $1,
            $2,
            'age_and_status',
            $3,
            $4,
            $5
        );
        """,
        contact_id,
        admin_id,
        old_status,
        status,
        f"Age: {age}",
    )

    return True


# =========================
# CLAIM / RELEASE
# =========================

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
          AND status != $3
          AND (
              claimed_by IS NULL
              OR claimed_by = $1
          );
        """,
        admin_id,
        contact_id,
        STATUS_NOT_WORKING,
    )

    if result.endswith("1"):
        await pool.execute(
            """
            INSERT INTO actions (
                contact_id,
                admin_id,
                action
            )
            VALUES (
                $1,
                $2,
                'claimed'
            );
            """,
            contact_id,
            admin_id,
        )

        return True

    return False


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

    if result.endswith("1"):
        await pool.execute(
            """
            INSERT INTO actions (
                contact_id,
                admin_id,
                action
            )
            VALUES (
                $1,
                $2,
                'released'
            );
            """,
            contact_id,
            admin_id,
        )

        return True

    return False


# =========================
# TIME / NOTES
# =========================

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


# =========================
# STATISTICS
# =========================

async def count_contacts(
    pool: asyncpg.Pool,
) -> int:
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


async def get_admin_statistics(
    pool: asyncpg.Pool,
    period: str = "all",
):
    """
    Return live statistics for every admin.

    period:
        today
        yesterday
        week
        all
    """

    if period == "today":
        date_filter = """
            AND a.created_at >= date_trunc(
                'day',
                now()
            )
        """

    elif period == "yesterday":
        date_filter = """
            AND a.created_at >= date_trunc(
                'day',
                now()
            ) - interval '1 day'
            AND a.created_at < date_trunc(
                'day',
                now()
            )
        """

    elif period == "week":
        date_filter = """
            AND a.created_at >= now() - interval '7 days'
        """

    else:
        date_filter = ""

    query = f"""
        SELECT
            a.admin_id,

            MAX(u.username) AS username,

            MAX(u.first_name) AS first_name,

            COUNT(
                DISTINCT a.contact_id
            ) FILTER (
                WHERE a.action = 'claimed'
            ) AS claimed_count,

            COUNT(
                DISTINCT a.contact_id
            ) FILTER (
                WHERE a.action = 'no_reply_48h'
            ) AS no_reply_count,

            COUNT(
                DISTINCT a.contact_id
            ) FILTER (
                WHERE (
                    a.action = 'status_change'
                    AND a.new_status = $1
                )
                OR (
                    a.action = 'age_and_status'
                    AND a.new_status = $1
                )
            ) AS refused_count,

            COUNT(
                DISTINCT a.contact_id
            ) FILTER (
                WHERE a.action = 'age_and_status'
                  AND a.new_status = $2
            ) AS under_16_count,

            COUNT(
                DISTINCT a.contact_id
            ) FILTER (
                WHERE a.action = 'status_change'
                  AND a.new_status = $3
            ) AS joined_count,

            COUNT(
                DISTINCT a.contact_id
            ) FILTER (
                WHERE a.action = 'status_change'
                  AND a.new_status = $4
            ) AS not_working_count,

            COUNT(
                DISTINCT a.contact_id
            ) FILTER (
                WHERE
                    a.action IN (
                        'claimed',
                        'no_reply_48h',
                        'status_change',
                        'age_and_status'
                    )
            ) AS processed_count

        FROM actions a

        LEFT JOIN users u
            ON u.telegram_id = a.admin_id

        WHERE a.admin_id IS NOT NULL
        {date_filter}

        GROUP BY a.admin_id

        ORDER BY
            processed_count DESC,
            a.admin_id ASC;
    """

    return await pool.fetch(
        query,
        STATUS_REFUSED,
        STATUS_UNDER_16,
        STATUS_JOINED,
        STATUS_NOT_WORKING,
    )


# =========================
# OLD USERS TABLE
# =========================

async def upsert_user(
    pool: asyncpg.Pool,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
) -> bool:
    """Insert or update a Telegram bot user."""

    result = await pool.fetchrow(
        """
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
            last_seen = now()

        RETURNING (xmax = 0) AS is_new;
        """,
        telegram_id,
        username,
        first_name,
    )

    return bool(result["is_new"])


# =========================
# CLOSE
# =========================

async def close_pool(
    pool: asyncpg.Pool,
) -> None:
    """Close PostgreSQL connection pool."""

    await pool.close()

    logger.info(
        "PostgreSQL pool closed."
    )
