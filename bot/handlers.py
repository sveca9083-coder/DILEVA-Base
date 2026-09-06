"""Telegram handlers for DILEVA Base."""

import logging
import os

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot import db

logger = logging.getLogger(__name__)

DB_KEY = "db"
REDIS_KEY = "redis"

STATUS_NEW = "new"
STATUS_NO_REPLY = "no_reply"
STATUS_REFUSED = "refused"
STATUS_UNDER_16 = "under_16"
STATUS_JOINED = "joined"
STATUS_LEFT = "left"
STATUS_NOT_WORKING = "not_working"

STATUS_NAMES = {
    STATUS_NEW: "🆕 Новые",
    STATUS_NO_REPLY: "⏳ Не отвечает",
    STATUS_REFUSED: "🚫 Отказано",
    STATUS_UNDER_16: "🔞 Нету 16",
    STATUS_JOINED: "✅ Вступил",
    STATUS_LEFT: "🚪 Вышел",
    STATUS_NOT_WORKING: "⛔ Не рабочие",
}

MENU_NEW = "🆕 Новые"
MENU_NO_REPLY = "⏳ Не отвечает"
MENU_REFUSED = "🚫 Отказано"
MENU_UNDER_16 = "🔞 Нету 16"
MENU_JOINED = "✅ Вступил"
MENU_LEFT = "🚪 Вышел"
MENU_NOT_WORKING = "⛔ Не рабочие"

MENU_SEARCH = "🔎 Поиск"
MENU_IMPORT = "📥 Импорт"
MENU_STATS = "📊 Статистика"


# =========================
# KEYBOARDS
# =========================

def get_menu_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [MENU_NEW, MENU_NO_REPLY],
        [MENU_REFUSED, MENU_UNDER_16],
        [MENU_JOINED, MENU_LEFT],
        [MENU_NOT_WORKING],
        [MENU_SEARCH, MENU_IMPORT],
        [MENU_STATS],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
    )


def contact_keyboard(
    contact_id: int,
    status: str = STATUS_NEW,
) -> InlineKeyboardMarkup:

    rows = [
        [
            InlineKeyboardButton(
                "🔒 Взять в работу",
                callback_data=f"claim:{contact_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "⏳ Не отвечает",
                callback_data=f"no_reply:{contact_id}",
            ),
            InlineKeyboardButton(
                "🚫 Отказ",
                callback_data=f"refused:{contact_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔞 Нету 16",
                callback_data=f"under16:{contact_id}",
            ),
            InlineKeyboardButton(
                "✅ Вступил",
                callback_data=f"joined:{contact_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "⛔ Не рабочий",
                callback_data=f"not_working:{contact_id}",
            )
        ],
    ]

    if status == STATUS_NOT_WORKING:
        rows = [
            [
                InlineKeyboardButton(
                    "♻️ Вернуть в новые",
                    callback_data=f"return_new:{contact_id}",
                )
            ]
        ]

    return InlineKeyboardMarkup(rows)


# =========================
# ADMIN
# =========================

def get_admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "")

    result = set()

    for value in raw.split(","):
        value = value.strip()

        if not value:
            continue

        try:
            result.add(int(value))
        except ValueError:
            logger.warning(
                "Invalid ADMIN_IDS value: %s",
                value,
            )

    return result


async def require_admin(update: Update) -> bool:
    """
    Allow DILEVA Base commands only to admins
    and only in private chat.

    In groups the bot stays completely silent.
    """

    user = update.effective_user
    message = update.effective_message

    if user is None:
        return False

    if message is not None:
        if message.chat.type != "private":
            return False

    if user.id not in get_admin_ids():
        if message is not None:
            await message.reply_text(
                "⛔ У тебя нет доступа к DILEVA Base."
            )

        return False

    return True


# =========================
# START
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    user = update.effective_user
    message = update.effective_message

    if user is None or message is None:
        return

    pool = context.bot_data.get(DB_KEY)

    if pool is not None:
        try:
            await db.upsert_user(
                pool,
                user.id,
                user.username,
                user.first_name,
            )
        except Exception:
            logger.exception(
                "Failed to save admin."
            )

    context.user_data.clear()

    await message.reply_text(
        "👋 Добро пожаловать в DILEVA Base.\n\n"
        "Выбирай раздел:",
        reply_markup=get_menu_keyboard(),
    )


# =========================
# AUTOMATIC CHAT MEMBER
# =========================

async def chat_member_update(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    chat_member = update.chat_member

    if chat_member is None:
        return

    old_status = chat_member.old_chat_member.status
    new_status = chat_member.new_chat_member.status

    joined_statuses = {
        "member",
        "administrator",
        "creator",
    }

    left_statuses = {
        "left",
        "kicked",
    }

    user = chat_member.new_chat_member.user

    if user is None:
        return

    if user.is_bot:
        return

    pool = context.bot_data.get(DB_KEY)

    if pool is None:
        logger.error(
            "Chat member update detected but database is unavailable."
        )
        return

    try:

        # =========================
        # USER JOINED
        # =========================

        if new_status in joined_statuses:

            if old_status in joined_statuses:
                return

            contact = await db.get_contact_by_telegram_id(
                pool,
                user.id,
            )

            if contact is None and user.username:
                contact = await db.get_contact_by_username(
                    pool,
                    user.username,
                )

            if contact is None:

                if not user.username:
                    logger.info(
                        "User %s joined without username.",
                        user.id,
                    )
                    return

                contact = await db.add_contact(
                    pool,
                    username=user.username,
                    source="chat_join",
                )

            if contact is None:
                logger.error(
                    "Failed to create/find contact for user %s.",
                    user.id,
                )
                return

            await db.update_telegram_user(
                pool,
                contact["id"],
                telegram_id=user.id,
                username=user.username,
                first_name=user.first_name,
            )

            await db.update_status(
                pool,
                contact["id"],
                STATUS_JOINED,
                admin_id=None,
                note="Automatic chat join detection",
            )

            if contact["claimed_by"]:
                await db.release_contact(
                    pool,
                    contact["id"],
                    contact["claimed_by"],
                )

            logger.info(
                "User @%s (%s) automatically moved to joined.",
                user.username,
                user.id,
            )

            return

        # =========================
        # USER LEFT
        # =========================

        if new_status in left_statuses:

            if old_status in left_statuses:
                return

            contact = await db.get_contact_by_telegram_id(
                pool,
                user.id,
            )

            if contact is None and user.username:
                contact = await db.get_contact_by_username(
                    pool,
                    user.username,
                )

            if contact is None:
                logger.info(
                    "User %s left but was not found.",
                    user.id,
                )
                return

            await db.update_telegram_user(
                pool,
                contact["id"],
                telegram_id=user.id,
                username=user.username,
                first_name=user.first_name,
            )

            await db.update_status(
                pool,
                contact["id"],
                STATUS_LEFT,
                admin_id=None,
                note="Automatic chat leave detection",
            )

            if contact["claimed_by"]:
                await db.release_contact(
                    pool,
                    contact["id"],
                    contact["claimed_by"],
                )

            logger.info(
                "User @%s (%s) automatically moved to left.",
                user.username,
                user.id,
            )

            return

    except Exception:
        logger.exception(
            "Failed to process chat member update for user %s.",
            user.id,
        )


# =========================
# MENU
# =========================

async def menu_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    message = update.effective_message

    if message is None or not message.text:
        return

    text = message.text.strip()

    if text == MENU_NEW:
        await show_status(update, context, STATUS_NEW)

    elif text == MENU_NO_REPLY:
        await show_status(update, context, STATUS_NO_REPLY)

    elif text == MENU_REFUSED:
        await show_status(update, context, STATUS_REFUSED)

    elif text == MENU_UNDER_16:
        await show_status(update, context, STATUS_UNDER_16)

    elif text == MENU_JOINED:
        await show_status(update, context, STATUS_JOINED)

    elif text == MENU_LEFT:
        await show_status(update, context, STATUS_LEFT)

    elif text == MENU_NOT_WORKING:
        await show_status(update, context, STATUS_NOT_WORKING)

    elif text == MENU_SEARCH:
        await search_start(update, context)

    elif text == MENU_IMPORT:
        await import_start(update, context)

    elif text == MENU_STATS:
        await statistics(update, context)


# =========================
# STATUS LIST
# =========================

async def show_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    status: str,
) -> None:

    if not await require_admin(update):
        return

    message = update.effective_message
    pool = context.bot_data.get(DB_KEY)

    if message is None:
        return

    if pool is None:
        await message.reply_text(
            "⚠️ База данных сейчас недоступна."
        )
        return

    try:
        contacts = await db.get_contacts_by_status(
            pool,
            status,
            limit=50,
        )

    except Exception:
        logger.exception(
            "Failed to load contacts."
        )

        await message.reply_text(
            "❌ Не удалось загрузить список."
        )
        return

    title = STATUS_NAMES.get(
        status,
        status,
    )

    if not contacts:
        await message.reply_text(
            f"{title}\n\n"
            "Пока здесь никого нет."
        )
        return

    await message.reply_text(
        f"{title}\n\n"
        "Контакты:"
    )

    for contact in contacts:

        text = f"👤 @{contact['username']}"

        if contact["age"] is not None:
            text += (
                f"\n🎂 Возраст: "
                f"{contact['age']}"
            )

        if contact["claimed_by"]:

            admin_name = await db.get_admin_display_name(
                pool,
                contact["claimed_by"],
            )

            text += (
                "\n🔒 Занят админом: "
                f"{admin_name}"
            )

        await message.reply_text(
            text,
            reply_markup=contact_keyboard(
                contact["id"],
                contact["status"],
            ),
        )


# =========================
# CONTACT CALLBACKS
# =========================

async def contact_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    query = update.callback_query

    if query is None:
        return

    user = query.from_user

    if user.id not in get_admin_ids():
        await query.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )
        return

    await query.answer()

    pool = context.bot_data.get(DB_KEY)

    if pool is None:
        await query.answer(
            "⚠️ База недоступна.",
            show_alert=True,
        )
        return

    data = query.data or ""

    try:
        action, contact_id_raw = data.split(
            ":",
            1,
        )

        contact_id = int(contact_id_raw)

    except (ValueError, AttributeError):
        await query.answer(
            "❌ Некорректная команда.",
            show_alert=True,
        )
        return

    contact = await db.get_contact(
        pool,
        contact_id,
    )

    if contact is None:
        await query.answer(
            "❌ Контакт не найден.",
            show_alert=True,
        )
        return

    # =========================
    # CLAIM
    # =========================

    if action == "claim":

        if contact["status"] == STATUS_NOT_WORKING:
            await query.answer(
                "⛔ Этот контакт помечен как не рабочий.",
                show_alert=True,
            )
            return

        if contact["claimed_by"] not in (
            None,
            user.id,
        ):
            admin_name = await db.get_admin_display_name(
                pool,
                contact["claimed_by"],
            )

            await query.answer(
                f"🔒 Уже занят: {admin_name}",
                show_alert=True,
            )
            return

        success = await db.claim_contact(
            pool,
            contact_id,
            user.id,
        )

        if success:
            admin_name = await db.get_admin_display_name(
                pool,
                user.id,
            )

            await query.answer(
                f"🔒 Закреплено за {admin_name}."
            )

        else:
            await query.answer(
                "🔒 Его уже забрал другой админ.",
                show_alert=True,
            )

        return

    # =========================
    # RETURN TO NEW
    # =========================

    if action == "return_new":

        if contact["status"] != STATUS_NOT_WORKING:
            await query.answer(
                "ℹ️ Контакт уже не в папке «Не рабочие».",
                show_alert=True,
            )
            return

        success = await db.return_to_new(
            pool,
            contact_id,
            user.id,
        )

        if not success:
            await query.answer(
                "❌ Не удалось вернуть контакт.",
                show_alert=True,
            )
            return

        await query.edit_message_text(
            f"👤 @{contact['username']}\n\n"
            "📌 Статус: 🆕 Новые"
        )

        return

    # =========================
    # OTHER ACTIONS
    # =========================

    if contact["status"] == STATUS_NOT_WORKING:
        await query.answer(
            "⛔ Сначала верни контакт в «Новые».",
            show_alert=True,
        )
        return

    if contact["claimed_by"] not in (
        None,
        user.id,
    ):
        admin_name = await db.get_admin_display_name(
            pool,
            contact["claimed_by"],
        )

        await query.answer(
            f"🔒 Этот контакт обрабатывает {admin_name}.",
            show_alert=True,
        )
        return

    # =========================
    # NO REPLY
    # =========================

    if action == "no_reply":

        await db.set_no_reply(
            pool,
            contact_id,
            user.id,
        )

        await query.edit_message_text(
            f"👤 @{contact['username']}\n\n"
            "📌 Статус: ⏳ Не отвечает\n"
            "⏰ Таймер 48 часов запущен."
        )

        return

    # =========================
    # REFUSED
    # =========================

    if action == "refused":

        await db.update_status(
            pool,
            contact_id,
            STATUS_REFUSED,
            user.id,
        )

        await db.release_contact(
            pool,
            contact_id,
            user.id,
        )

        await query.edit_message_text(
            f"👤 @{contact['username']}\n\n"
            "📌 Статус: 🚫 Отказано"
        )

        return

    # =========================
    # UNDER 16
    # =========================

    if action == "under16":

        context.user_data["waiting_age"] = contact_id

        await query.message.reply_text(
            f"🔞 Введи возраст для "
            f"@{contact['username']}.\n\n"
            "Например: 15"
        )

        return

    # =========================
    # JOINED
    # =========================

    if action == "joined":

        await db.update_status(
            pool,
            contact_id,
            STATUS_JOINED,
            user.id,
        )

        await db.release_contact(
            pool,
            contact_id,
            user.id,
        )

        await query.edit_message_text(
            f"👤 @{contact['username']}\n\n"
            "📌 Статус: ✅ Вступил"
        )

        return

    # =========================
    # NOT WORKING
    # =========================

    if action == "not_working":

        await db.set_not_working(
            pool,
            contact_id,
            user.id,
        )

        await query.edit_message_text(
            f"👤 @{contact['username']}\n\n"
            "📌 Статус: ⛔ Не рабочий"
        )

        return


# =========================
# AGE INPUT
# =========================

async def handle_age_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:

    message = update.effective_message
    pool = context.bot_data.get(DB_KEY)

    if message is None or pool is None:
        return False

    contact_id = context.user_data.get(
        "waiting_age"
    )

    if contact_id is None:
        return False

    text = message.text.strip()

    if not text.isdigit():
        await message.reply_text(
            "❌ Введи возраст только цифрами.\n"
            "Например: 15"
        )
        return True

    age = int(text)

    if age < 1 or age > 120:
        await message.reply_text(
            "❌ Некорректный возраст."
        )
        return True

    contact = await db.get_contact(
        pool,
        contact_id,
    )

    if contact is None:
        context.user_data.pop(
            "waiting_age",
            None,
        )

        await message.reply_text(
            "❌ Контакт не найден."
        )
        return True

    if age < 16:

        await db.set_age_and_status(
            pool,
            contact_id,
            age,
            STATUS_UNDER_16,
            update.effective_user.id,
        )

        result = "🔞 Нету 16"

    else:

        await db.set_age_and_status(
            pool,
            contact_id,
            age,
            STATUS_NO_REPLY,
            update.effective_user.id,
        )

        result = (
            "⏳ Не отвечает\n"
            "⏰ Таймер 48 часов запущен."
        )

    context.user_data.pop(
        "waiting_age",
        None,
    )

    await message.reply_text(
        f"👤 @{contact['username']}\n\n"
        f"🎂 Возраст: {age}\n"
        f"📌 Статус: {result}"
    )

    return True


# =========================
# SEARCH
# =========================

async def search_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    message = update.effective_message

    if message is None:
        return

    context.user_data["mode"] = "search"

    await message.reply_text(
        "🔎 Введи username.\n\n"
        "Например:\n"
        "@username"
    )


async def search_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    message = update.effective_message
    pool = context.bot_data.get(DB_KEY)

    if message is None or pool is None:
        return

    username = message.text.strip()

    contact = await db.get_contact_by_username(
        pool,
        username,
    )

    context.user_data.pop(
        "mode",
        None,
    )

    if contact is None:
        await message.reply_text(
            f"🔎 @{username.lstrip('@')}\n\n"
            "❌ Пользователь не найден."
        )
        return

    text = (
        "🔎 Найден пользователь\n\n"
        f"👤 @{contact['username']}\n"
        f"📌 Статус: "
        f"{STATUS_NAMES.get(contact['status'], contact['status'])}"
    )

    if contact["age"] is not None:
        text += (
            f"\n🎂 Возраст: {contact['age']}"
        )

    if contact["claimed_by"]:

        admin_name = await db.get_admin_display_name(
            pool,
            contact["claimed_by"],
        )

        text += (
            "\n🔒 Занят админом: "
            f"{admin_name}"
        )

    if contact["notes"]:
        text += (
            f"\n📝 {contact['notes']}"
        )

    await message.reply_text(
        text,
        reply_markup=contact_keyboard(
            contact["id"],
            contact["status"],
        ),
    )


# =========================
# IMPORT
# =========================

async def import_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    message = update.effective_message

    if message is None:
        return

    context.user_data["mode"] = "import"

    await message.reply_text(
        "📥 Отправь username по одному в строке:\n\n"
        "@user1\n"
        "@user2\n"
        "@user3"
    )


async def import_users(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    message = update.effective_message
    pool = context.bot_data.get(DB_KEY)

    if message is None or pool is None:
        return

    usernames = []

    for line in message.text.splitlines():
        username = line.strip().lstrip("@")

        if username:
            usernames.append(username)

    unique = []
    seen = set()

    for username in usernames:

        normalized = username.lower()

        if normalized in seen:
            continue

        seen.add(normalized)
        unique.append(username)

    added = 0
    skipped = 0

    for username in unique:

        existing = await db.get_contact_by_username(
            pool,
            username,
        )

        if existing is not None:
            skipped += 1
            continue

        contact = await db.add_contact(
            pool,
            username,
            added_by=update.effective_user.id,
            source="import",
        )

        if contact is not None:
            added += 1
        else:
            skipped += 1

    context.user_data.pop(
        "mode",
        None,
    )

    await message.reply_text(
        "📥 Импорт завершён.\n\n"
        f"✅ Добавлено: {added}\n"
        f"⏭ Пропущено: {skipped}"
    )


# =========================
# STATISTICS
# =========================

def statistics_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📅 Сегодня",
                    callback_data="stats:today",
                ),
                InlineKeyboardButton(
                    "📆 Вчера",
                    callback_data="stats:yesterday",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🗓 7 дней",
                    callback_data="stats:week",
                ),
                InlineKeyboardButton(
                    "📈 Всё время",
                    callback_data="stats:all",
                ),
            ],
        ]
    )


def period_name(period: str) -> str:
    names = {
        "today": "📅 Сегодня",
        "yesterday": "📆 Вчера",
        "week": "🗓 За 7 дней",
        "all": "📈 За всё время",
    }

    return names.get(
        period,
        "📈 За всё время",
    )


async def build_statistics_text(
    pool,
    period: str = "all",
) -> str:

    total = await db.count_contacts(pool)

    new_count = await db.count_by_status(
        pool,
        STATUS_NEW,
    )

    no_reply_count = await db.count_by_status(
        pool,
        STATUS_NO_REPLY,
    )

    refused_count = await db.count_by_status(
        pool,
        STATUS_REFUSED,
    )

    under_16_count = await db.count_by_status(
        pool,
        STATUS_UNDER_16,
    )

    joined_count = await db.count_by_status(
        pool,
        STATUS_JOINED,
    )

    left_count = await db.count_by_status(
        pool,
        STATUS_LEFT,
    )

    not_working_count = await db.count_by_status(
        pool,
        STATUS_NOT_WORKING,
    )

    admin_stats = await db.get_admin_statistics(
        pool,
        period=period,
    )

    text = (
        "📊 Статистика DILEVA Base\n\n"
        f"👥 Всего: {total}\n\n"
        f"🆕 Новые: {new_count}\n"
        f"⏳ Не отвечает: {no_reply_count}\n"
        f"🚫 Отказано: {refused_count}\n"
        f"🔞 Нету 16: {under_16_count}\n"
        f"✅ Вступил: {joined_count}\n"
        f"🚪 Вышел: {left_count}\n"
        f"⛔ Не рабочие: {not_working_count}\n\n"
        f"👮 Отчёт по админам — "
        f"{period_name(period)}\n"
    )

    if admin_stats:

        for admin in admin_stats:

            admin_id = admin["admin_id"]
            username = admin["username"]
            first_name = admin["first_name"]

            if username:
                admin_name = f"@{username}"

            elif first_name:
                admin_name = first_name

            else:
                admin_name = f"ID {admin_id}"

            text += (
                f"\n👤 {admin_name}\n"
                f"├─ 📊 Обработано: "
                f"{admin['processed_count']}\n"
                f"├─ 🔒 Взял в работу: "
                f"{admin['claimed_count']}\n"
                f"├─ ⏳ Не отвечает: "
                f"{admin['no_reply_count']}\n"
                f"├─ 🚫 Отказано: "
                f"{admin['refused_count']}\n"
                f"├─ 🔞 Нету 16: "
                f"{admin['under_16_count']}\n"
                f"├─ ⛔ Не рабочие: "
                f"{admin['not_working_count']}\n"
                f"└─ ✅ Вступил: "
                f"{admin['joined_count']}\n"
            )

    else:
        text += (
            "\n\nПока действий админов "
            "за этот период нет."
        )

    return text


async def statistics(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    message = update.effective_message
    pool = context.bot_data.get(DB_KEY)

    if message is None or pool is None:
        return

    try:
        text = await build_statistics_text(
            pool,
            period="all",
        )

    except Exception:
        logger.exception(
            "Statistics error."
        )

        await message.reply_text(
            "❌ Не удалось получить статистику."
        )
        return

    await message.reply_text(
        text,
        reply_markup=statistics_keyboard(),
    )


async def statistics_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    query = update.callback_query

    if query is None:
        return

    if query.from_user.id not in get_admin_ids():
        await query.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )
        return

    await query.answer()

    pool = context.bot_data.get(DB_KEY)

    if pool is None:
        await query.answer(
            "⚠️ База недоступна.",
            show_alert=True,
        )
        return

    data = query.data or ""

    try:
        _, period = data.split(
            ":",
            1,
        )
    except ValueError:
        await query.answer(
            "❌ Некорректный период.",
            show_alert=True,
        )
        return

    if period not in {
        "today",
        "yesterday",
        "week",
        "all",
    }:
        await query.answer(
            "❌ Неизвестный период.",
            show_alert=True,
        )
        return

    try:
        text = await build_statistics_text(
            pool,
            period=period,
        )

        await query.edit_message_text(
            text,
            reply_markup=statistics_keyboard(),
        )

    except Exception:
        logger.exception(
            "Statistics callback error."
        )

        await query.answer(
            "❌ Не удалось обновить статистику.",
            show_alert=True,
        )


# =========================
# TEXT ROUTER
# =========================

async def text_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    if await handle_age_input(
        update,
        context,
    ):
        return

    mode = context.user_data.get(
        "mode"
    )

    if mode == "search":
        await search_user(
            update,
            context,
        )
        return

    if mode == "import":
        await import_users(
            update,
            context,
        )
        return


# =========================
# AUTOMATIC 48 HOURS
# =========================

async def expiration_job(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    pool = context.bot_data.get(DB_KEY)

    if pool is None:
        return

    try:

        count = await db.return_expired_no_reply(
            pool
        )

        if count:
            logger.info(
                "Returned %s contacts to new queue.",
                count,
            )

    except Exception:
        logger.exception(
            "Expiration job failed."
        )


# =========================
# ERROR
# =========================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    logger.error(
        "Unhandled exception:",
        exc_info=context.error,
    )


# =========================
# COMMANDS
# =========================

async def set_bot_commands(
    application: Application,
) -> None:

    await application.bot.set_my_commands(
        [
            (
                "start",
                "Запустить DILEVA Base",
            ),
        ]
    )


# =========================
# REGISTER
# =========================

def register_handlers(
    application: Application,
) -> None:

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        ChatMemberHandler(
            chat_member_update,
            ChatMemberHandler.CHAT_MEMBER,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            statistics_callback,
            pattern=r"^stats:(today|yesterday|week|all)$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            contact_callback,
            pattern=r"^(claim|no_reply|refused|under16|joined|not_working|return_new):\d+$",
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(
                r"^(🆕 Новые|⏳ Не отвечает|🚫 Отказано|🔞 Нету 16|✅ Вступил|🚪 Вышел|⛔ Не рабочие|🔎 Поиск|📥 Импорт|📊 Статистика)$"
            ),
            menu_button,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_router,
        )
    )

    if application.job_queue is not None:
        application.job_queue.run_repeating(
            expiration_job,
            interval=300,
            first=10,
        )
