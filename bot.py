
import asyncio
import json
import logging
import logging.handlers
import os
import random
import re
import string
import time
from datetime import datetime
from pathlib import Path

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

# ══════════════════════════════════════════════
#  CONFIG  ← Only edit BOT_TOKEN and API_KEY
# ══════════════════════════════════════════════
BOT_TOKEN        = "8602583322:AAHDlK02GGHZI8xkE_pvhHpiGe7wnpsE3fQ"
ADMIN_PASSWORD   = os.getenv("ADMIN_PASS", "ASHU22/01/2007")  # set env ADMIN_PASS to override
CHANNEL_USERNAME = "IG_LOOTERS"
CHANNEL_LINK     = "https://t.me/IG_LOOTERS"
CONTACT_LINK     = "https://t.me/ashuh4reee"

API_URL          = "https://smmvault.in/api/v2"
API_KEY          = "fe242d6e22fb5637d4f5a8d2b53088071250d854"
SERVICE_ID       = 1658

QR_IMAGE_PATH = "https://i.ibb.co/6cDYBprB/Screenshot-2026-05-02-23-14-16-36-944a2809ea1b4cda6ef12d1db9048ed3.jpg"
# local file path OR https:// URL

AUTO_VIEWS_QTY   = 110             # views per new channel post

PRICE_PER_1000 = 10          # Rs per 1000 views
VIEW_PRESETS   = [100, 500, 1000, 2000, 5000]
VIEW_MIN       = 10
VIEW_MAX       = 100_000


def calc_price(views: int) -> float:
    """Return price in Rs rounded to 2 decimal places."""
    return round((views / 1000) * PRICE_PER_1000, 2)

DATA_FILE          = "ashu_data.json"
LOG_FILE           = "ashu_bot.log"
PAYMENT_EXPIRY_SEC = 900    # 15 minutes
ORDER_COOLDOWN_SEC = 10     # seconds between orders per user
DAILY_ORDER_LIMIT  = 10     # max orders per user per day
MEMBER_CACHE_SEC   = 8      # channel membership cache TTL
# ══════════════════════════════════════════════

# ── Logging setup ─────────────────────────────
_log_fmt      = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
_file_handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3)
_file_handler.setFormatter(_log_fmt)
_con_handler  = logging.StreamHandler()
_con_handler.setFormatter(_log_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _con_handler])
logger = logging.getLogger(__name__)

# ── In-memory caches ──────────────────────────
_member_cache: dict = {}   # uid -> (is_member: bool, ts: float)
_cmd_spam:     dict = {}   # uid -> last_ts: float


# ══════════════════════════════════════════════
#  DATA LAYER
# ══════════════════════════════════════════════

def _default_data() -> dict:
    return {
        "users":           {},
        "payments":        {},
        "admins":          [],
        "logs":            [],
        "orders_log":      [],   # global order history
        "auto_views":      False, # auto-views feature enabled/disabled
        "auto_views_log":  [],   # processed message_ids to prevent duplicates
    }


def load_data() -> dict:
    if Path(DATA_FILE).exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            for k, v in _default_data().items():
                d.setdefault(k, v)
            return d
        except Exception:
            pass
    return _default_data()


def save_data(data: dict) -> None:
    """Atomic write: write to temp file then replace, preventing corruption on crash."""
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DATA_FILE)


def _uid(uid: int) -> str:
    return str(uid)


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ── User helpers ──────────────────────────────

def register_user(data: dict, uid: int) -> None:
    s = _uid(uid)
    if s not in data["users"]:
        data["users"][s] = {
            "balance":         0,
            "joined":          _now_str(),
            "orders_today":    0,
            "last_order_date": "",
            "last_order_ts":   0,
            "tx_log":          [],
        }
        save_data(data)


def get_balance(data: dict, uid: int) -> int:
    return int(data["users"].get(_uid(uid), {}).get("balance", 0))


def set_balance(data: dict, uid: int, amount: int) -> None:
    amount = max(0, int(amount))
    s = _uid(uid)
    if s not in data["users"]:
        register_user(data, uid)
    data["users"][s]["balance"] = amount


def add_balance(data: dict, uid: int, amount: int) -> None:
    amount = max(0, int(amount))
    set_balance(data, uid, get_balance(data, uid) + amount)
    _log_tx(data, uid, f"+Rs{amount} deposit approved")


def deduct_balance(data: dict, uid: int, amount: int) -> bool:
    amount = max(0, int(amount))
    cur = get_balance(data, uid)
    if cur < amount:
        return False
    set_balance(data, uid, cur - amount)
    _log_tx(data, uid, f"-Rs{amount} order placed")
    return True


def _log_tx(data: dict, uid: int, note: str) -> None:
    s = _uid(uid)
    if s not in data["users"]:
        return
    log = data["users"][s].setdefault("tx_log", [])
    log.append({"ts": _now_str(), "note": note})
    data["users"][s]["tx_log"] = log[-20:]


# ── Payment helpers ───────────────────────────

def gen_payment_id(data: dict) -> str:
    while True:
        pid = "TXN" + "".join(random.choices(string.digits, k=5))
        if pid not in data["payments"]:
            return pid


def get_active_payment(data: dict, uid: int) -> str | None:
    """Return active non-expired PID for user, or None. Marks expired ones."""
    now = time.time()
    for pid, p in data["payments"].items():
        if p["user_id"] != uid:
            continue
        if p["status"] not in ("pending", "pending_confirmation"):
            continue
        if now - p.get("created_ts", 0) > PAYMENT_EXPIRY_SEC:
            data["payments"][pid]["status"] = "expired"
            continue
        return pid
    return None


def is_payment_expired(p: dict) -> bool:
    return time.time() - p.get("created_ts", 0) > PAYMENT_EXPIRY_SEC


# ── Order throttle helpers ────────────────────

def check_order_allowed(data: dict, uid: int) -> tuple:
    s = _uid(uid)
    u = data["users"].get(s, {})

    if u.get("last_order_date") != _today_str():
        data["users"][s]["orders_today"]    = 0
        data["users"][s]["last_order_date"] = _today_str()

    since_last = time.time() - u.get("last_order_ts", 0)
    if since_last < ORDER_COOLDOWN_SEC:
        wait = int(ORDER_COOLDOWN_SEC - since_last) + 1
        return False, f"Please wait *{wait}s* before placing another order."

    if u.get("orders_today", 0) >= DAILY_ORDER_LIMIT:
        return False, f"Daily order limit (*{DAILY_ORDER_LIMIT} orders*) reached. Try tomorrow."

    return True, ""


def record_order(data: dict, uid: int) -> None:
    s = _uid(uid)
    data["users"][s]["last_order_ts"]   = time.time()
    data["users"][s]["orders_today"]    = data["users"][s].get("orders_today", 0) + 1
    data["users"][s]["last_order_date"] = _today_str()


def has_recent_order_for_link(data: dict, uid: int, link: str) -> bool:
    """Return True if user placed an order for this exact link in the last 24h."""
    cutoff = time.time() - 86400
    for entry in data.get("orders_log", []):
        if entry.get("user_id") == uid and entry.get("link") == link:
            if entry.get("ts", 0) >= cutoff:
                return True
    return False


def log_order(data: dict, uid: int, qty: int, link: str, order_id) -> None:
    """Append entry to global orders_log, keep last 50."""
    data.setdefault("orders_log", []).append({
        "user_id":  uid,
        "quantity": qty,
        "link":     link,
        "order_id": str(order_id),
        "ts":       time.time(),
        "ts_str":   _now_str(),
    })
    data["orders_log"] = data["orders_log"][-50:]


# ── Admin helpers ─────────────────────────────

def load_admin_ids(data: dict) -> set:
    return set(data.get("admins", []))


def save_admin_ids(data: dict, ids: set) -> None:
    data["admins"] = list(ids)


def push_log(data: dict, entry: str) -> None:
    data.setdefault("logs", []).append({"ts": _now_str(), "msg": entry})
    data["logs"] = data["logs"][-50:]


CLEANUP_MAX_AGE_SEC = 86400  # 24 hours


def cleanup_old_payments(data: dict) -> int:
    """Remove expired/rejected payments older than 24h. Returns count removed."""
    cutoff = time.time() - CLEANUP_MAX_AGE_SEC
    to_del = [
        pid for pid, p in data["payments"].items()
        if p["status"] in ("expired", "rejected")
        and p.get("created_ts", 0) < cutoff
    ]
    for pid in to_del:
        del data["payments"][pid]
    return len(to_del)


def total_revenue(data: dict) -> int:
    return sum(
        int(p.get("amount", 0))
        for p in data["payments"].values()
        if p.get("status") == "approved"
    )


# ══════════════════════════════════════════════
#  UTR VALIDATION
# ══════════════════════════════════════════════

UTR_MIN_DIGITS = 12


def validate_utr(utr: str) -> tuple:
    """Returns (ok: bool, reason: str)."""
    if not utr.isdigit():
        return False, "UTR must contain only digits (no letters or symbols)."
    if len(utr) < UTR_MIN_DIGITS:
        return False, f"UTR must be at least {UTR_MIN_DIGITS} digits (got {len(utr)})."
    if len(set(utr)) == 1:
        return False, "UTR is invalid (all digits are the same)."
    if utr.startswith("000"):
        return False, "UTR is invalid (cannot start with 000)."
    return True, ""


def is_duplicate_utr(data: dict, utr: str) -> bool:
    """Return True if UTR was already used in any payment."""
    return any(p.get("utr") == utr for p in data["payments"].values())


# ══════════════════════════════════════════════
#  MEMBERSHIP CACHE
# ══════════════════════════════════════════════

async def is_member(bot, uid: int) -> bool:
    now = time.time()
    if uid in _member_cache:
        val, ts = _member_cache[uid]
        if now - ts < MEMBER_CACHE_SEC:
            return val
    try:
        m = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", uid)
        result = m.status in ("member", "administrator", "creator")
    except Exception:
        result = False
    _member_cache[uid] = (result, now)
    return result


def invalidate_member_cache(uid: int) -> None:
    _member_cache.pop(uid, None)


# ══════════════════════════════════════════════
#  SPAM GUARD
# ══════════════════════════════════════════════

def spam_check(uid: int, gap: float = 1.5) -> bool:
    now = time.time()
    if now - _cmd_spam.get(uid, 0) < gap:
        return False
    _cmd_spam[uid] = now
    return True


# ══════════════════════════════════════════════
#  UI HELPERS
# ══════════════════════════════════════════════

def not_joined_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)],
        [InlineKeyboardButton("🔄 Check Again", callback_data="check_join")],
    ])


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💰 Deposit",     callback_data="deposit"),
            InlineKeyboardButton("🚀 Order Views", callback_data="order_start"),
        ],
        [
            InlineKeyboardButton("💳 Balance",     callback_data="balance"),
            InlineKeyboardButton("📖 How To Use",  callback_data="howto"),
        ],
        [InlineKeyboardButton("📞 Contact Admin", url=CONTACT_LINK)],
    ])


def main_menu_text(name: str) -> str:
    return (
        f"⚡ *ASHU PANEL* ⚡\n\n"
        f"Welcome, *{name}*! 👋\n\n"
        f"🎯 Boost your Telegram posts with real views.\n"
        f"💸 Fast delivery · Secure payments · 24/7 support\n\n"
        f"Choose an option below 👇"
    )


def qty_keyboard(link: str) -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(VIEW_PRESETS), 2):
        row = []
        for q in VIEW_PRESETS[i:i+2]:
            price = calc_price(q)
            label = f"{q} Views — ₹{price:g}"
            row.append(InlineKeyboardButton(label, callback_data=f"qty|{q}|{link}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Custom Views", callback_data=f"qty|custom|{link}")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="back_menu")])
    return InlineKeyboardMarkup(rows)


def confirm_keyboard(qty: int, link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm Order", callback_data=f"confirm|{qty}|{link}")],
        [InlineKeyboardButton("❌ Cancel",        callback_data="back_menu")],
    ])


def _is_admin(ctx: ContextTypes.DEFAULT_TYPE, uid: int) -> bool:
    return uid in ctx.bot_data.get("admin_ids", set())


# ══════════════════════════════════════════════
#  HANDLERS
# ══════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not spam_check(user.id):
        return
    data = load_data()
    register_user(data, user.id)
    ctx.bot_data.setdefault("admin_ids", load_admin_ids(data))

    if not await is_member(ctx.bot, user.id):
        await update.message.reply_text(
            "🚫 *ACCESS DENIED*\n\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "📢 *Join Our Official Channel*\n\n"
            "To use this bot, you must join our channel first.\n\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "⚡ *Steps:*\n"
            "1. Tap *Join Channel* below\n"
            "2. Come back here\n"
            "3. Press *Check Again*\n\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "💡 *Note:* Access is locked until you join.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=not_joined_keyboard(),
        )
        return

    await update.message.reply_text(
        main_menu_text(user.first_name),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(),
    )


async def cb_check_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user  = query.from_user
    invalidate_member_cache(user.id)
    data  = load_data()
    register_user(data, user.id)
    ctx.bot_data.setdefault("admin_ids", load_admin_ids(data))

    if not await is_member(ctx.bot, user.id):
        await query.edit_message_text(
            "🚫 *ACCESS DENIED*\n\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "📢 *Join Our Official Channel*\n\n"
            "You haven't joined yet. Please join and try again.\n\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "⚡ *Steps:*\n"
            "1. Tap *Join Channel* below\n"
            "2. Come back here\n"
            "3. Press *Check Again*\n\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "💡 *Note:* Access is locked until you join.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=not_joined_keyboard(),
        )
        return

    await query.edit_message_text(
        main_menu_text(user.first_name),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(),
    )


async def cb_back_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data.pop("awaiting_link", None)
    await query.edit_message_text(
        main_menu_text(query.from_user.first_name),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(),
    )


async def cb_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = load_data()
    uid  = query.from_user.id
    bal  = get_balance(data, uid)
    tx   = data["users"].get(_uid(uid), {}).get("tx_log", [])[-3:]
    tx_lines = "\n".join(f"   • {t['ts']} — {t['note']}" for t in reversed(tx)) or "   No transactions yet."

    await query.edit_message_text(
        f"💳 *Your Balance*\n\n"
        f"┌─────────────────────\n"
        f"│ 💰 Available: Rs{bal}\n"
        f"└─────────────────────\n\n"
        f"📋 *Recent Transactions:*\n{tx_lines}\n\n"
        f"Use 💰 Deposit to add funds.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Deposit Now", callback_data="deposit")],
            [InlineKeyboardButton("🔙 Back",        callback_data="back_menu")],
        ]),
    )


async def cb_howto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📖 *How To Use ASHU PANEL*\n\n"
        "1️⃣ *Deposit Funds*\n"
        "   › Click 💰 Deposit\n"
        "   › Scan the UPI QR code\n"
        "   › Note the Payment ID shown\n"
        "   › Send: `/pay <payment_id> <UTR>`\n"
        "   › UTR = 12+ digit transaction number\n"
        "   › ID expires in *15 minutes*\n\n"
        "2️⃣ *Order Views*\n"
        "   › Click 🚀 Order Views\n"
        "   › Send your Telegram post link\n"
        "     Format: `https://t.me/channel/123`\n"
        "   › Review order summary & confirm\n"
        "   › Views delivered instantly! ⚡\n\n"
        "3️⃣ *Check Balance*\n"
        "   › Click 💳 Balance anytime\n\n"
        "4️⃣ *Auto Views (Channel Feature)*\n"
        "   › Add bot as admin in your channel\n"
        "   › Use /start_auto to enable\n"
        "   › Every new post will automatically receive 110 views\n"
        "   › Use /stop_auto to disable\n\n"
        f"❓ *Need help?* → {CONTACT_LINK}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
        ]),
    )


def deposit_amount_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("₹10",  callback_data="dep_amt|10"),
            InlineKeyboardButton("₹30",  callback_data="dep_amt|30"),
        ],
        [
            InlineKeyboardButton("₹50",  callback_data="dep_amt|50"),
            InlineKeyboardButton("₹70",  callback_data="dep_amt|70"),
        ],
        [
            InlineKeyboardButton("₹100", callback_data="dep_amt|100"),
            InlineKeyboardButton("₹150", callback_data="dep_amt|150"),
        ],
        [InlineKeyboardButton("₹200",    callback_data="dep_amt|200")],
        [InlineKeyboardButton("✏️ Custom Amount", callback_data="dep_amt|custom")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")],
    ])


async def cb_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = load_data()

    existing = get_active_payment(data, user.id)
    if existing:
        p = data["payments"][existing]
        remaining = max(0, PAYMENT_EXPIRY_SEC - int(time.time() - p.get("created_ts", 0)))
        mins, secs = divmod(remaining, 60)
        amt_display = f"₹{p['amount']}" if p.get("amount") else "N/A"
        save_data(data)
        await query.edit_message_text(
            f"⚠️ *Active Deposit Pending*\n\n"
            f"You already have an active payment:\n"
            f"🔑 ID: `{existing}`\n"
            f"💵 Amount: {amt_display}\n"
            f"⏱ Expires in: *{mins}m {secs}s*\n\n"
            f"Send `/pay {existing} <UTR>` after making payment.\n"
            f"Wait for expiry to create a new one.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")],
            ]),
        )
        return

    await query.edit_message_text(
        "💰 *Deposit Funds*\n\n"
        "Select the amount you want to deposit:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=deposit_amount_keyboard(),
    )


async def cb_deposit_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ Processing...")
    user = query.from_user
    data = load_data()

    raw_amt = query.data.split("|", 1)[1]

    if raw_amt == "custom":
        ctx.user_data["awaiting_custom_amount"] = True
        await query.edit_message_text(
            "✏️ *Custom Deposit Amount*\n\n"
            "Send the amount you want to deposit (numbers only).\n"
            "Example: `250`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="deposit")],
            ]),
        )
        return

    try:
        amount = int(raw_amt)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await query.answer("❌ Invalid amount.", show_alert=True)
        return

    await _create_deposit_payment(query, ctx, user, data, amount)


async def _create_deposit_payment(query, ctx, user, data, amount: int):
    """Create payment record and show QR/deposit instructions."""
    # Re-check for active payment (race guard)
    existing = get_active_payment(data, user.id)
    if existing:
        save_data(data)
        await query.edit_message_text(
            f"⚠️ You already have an active payment `{existing}`.\n"
            f"Wait for it to expire before creating a new one.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")],
            ]),
        )
        return

    pid = gen_payment_id(data)
    data["payments"][pid] = {
        "user_id":    user.id,
        "amount":     amount,
        "status":     "pending",
        "created_ts": time.time(),
        "created":    _now_str(),
        "username":   user.username or user.first_name,
    }
    save_data(data)
    logger.info("PAYMENT | New ID %s amount Rs%s for user %s", pid, amount, user.id)

    caption = (
        f"💰 *Deposit Funds*\n\n"
        f"┌────────────────────────\n"
        f"│ 💵 Amount: ₹{amount}\n"
        f"│ 🔑 Payment ID: `{pid}`\n"
        f"│ ⏳ Expires in: *15 minutes*\n"
        f"└────────────────────────\n\n"
        f"📲 *Steps:*\n"
        f"1. Scan the QR code above\n"
        f"2. Pay exactly ₹{amount} via UPI\n"
        f"3. Send: `/pay {pid} <UTR>`\n"
        f"   Example: `/pay {pid} 123456789012`\n\n"
        f"⚠️ *Rules:*\n"
        f"• Payment ID + UTR are mandatory\n"
        f"• UTR = 12+ digit number from your UPI app\n"
        f"• Only 1 active deposit allowed at a time\n"
        f"• ID expires after 15 minutes"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")],
    ])

    try:
        if QR_IMAGE_PATH.startswith("http"):
            await query.message.reply_photo(photo=QR_IMAGE_PATH, caption=caption,
                                             parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        elif Path(QR_IMAGE_PATH).exists():
            with open(QR_IMAGE_PATH, "rb") as f:
                await query.message.reply_photo(photo=f, caption=caption,
                                                 parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        else:
            raise FileNotFoundError
        await query.delete_message()
    except Exception:
        await query.edit_message_text(
            "💰 *Deposit Funds*\n\n"
            "📷 _(Set QR_IMAGE_PATH in config to show QR)_\n\n"
            + caption[caption.index("┌"):],
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb,
        )


async def cmd_pay(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not spam_check(user.id, 2.0):
        return
    args = ctx.args

    # ── Argument validation ──────────────────
    if len(args) < 2:
        await update.message.reply_text(
            "❌ *Incorrect format.*\n\n"
            "Usage: `/pay <payment_id> <UTR>`\n"
            "Example: `/pay TXN12345 123456789012`\n\n"
            "UTR = 12+ digit transaction number from your UPI app.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    pid = args[0].strip().upper()
    utr = args[1].strip()

    # ── UTR format check ────────────────────
    utr_ok, utr_reason = validate_utr(utr)
    if not utr_ok:
        await update.message.reply_text(
            f"❌ *Invalid UTR:* {utr_reason}\n\n"
            "Please provide the correct transaction ID from your UPI app.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    data = load_data()

    # ── Duplicate UTR check ─────────────────
    if is_duplicate_utr(data, utr):
        await update.message.reply_text(
            "❌ *Duplicate UTR.*\n\n"
            "This transaction ID has already been used.\n"
            "Contact admin if you believe this is an error.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if pid not in data["payments"]:
        await update.message.reply_text(
            "❌ *Invalid Payment ID.*\n\nGenerate a new one from 💰 Deposit.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    pmt = data["payments"][pid]

    if pmt["user_id"] != user.id:
        await update.message.reply_text("❌ This Payment ID does not belong to your account.")
        return

    if pmt["status"] == "expired" or is_payment_expired(pmt):
        data["payments"][pid]["status"] = "expired"
        save_data(data)
        await update.message.reply_text(
            "⌛ *Payment ID Expired.*\n\nThis ID expired (15-min limit).\n"
            "Go to 💰 Deposit to generate a new one.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if pmt["status"] == "pending_confirmation":
        await update.message.reply_text(
            f"⏳ *Already Submitted.*\n\n`{pid}` is awaiting admin approval.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if pmt["status"] in ("approved", "rejected"):
        await update.message.reply_text(
            f"ℹ️ Payment `{pid}` was already *{pmt['status']}*.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    data["payments"][pid]["status"]   = "pending_confirmation"
    data["payments"][pid]["username"] = user.username or user.first_name
    data["payments"][pid]["utr"]      = utr
    push_log(data, f"PAY_REQUEST | {pid} | UTR:{utr} | user {user.id} (@{user.username})")
    save_data(data)
    logger.info("PAYMENT | /pay submitted: %s UTR:%s by user %s", pid, utr, user.id)

    amt_display = f"₹{pmt['amount']}" if pmt.get("amount") else "N/A"
    await update.message.reply_text(
        f"✅ *Payment Submitted!*\n\n"
        f"🔑 Payment ID: `{pid}`\n"
        f"💵 Amount: {amt_display}\n"
        f"⏳ Status: Pending Admin Approval\n\n"
        f"You'll be notified once approved. 🙏",
        parse_mode=ParseMode.MARKDOWN,
    )

    admin_ids = ctx.bot_data.get("admin_ids", load_admin_ids(data))
    for aid in list(admin_ids):
        try:
            await ctx.bot.send_message(
                aid,
                f"🔔 *New Payment Request*\n\n"
                f"👤 User: {user.first_name} (`{user.id}`)\n"
                f"🔗 Username: @{user.username or 'N/A'}\n"
                f"🔑 Payment ID: `{pid}`\n"
                f"💵 Amount: {amt_display}\n"
                f"🏦 UTR: `{utr}`\n\n"
                f"Approve: `/approve {pid} <amount>`\n"
                f"Reject:  `/reject {pid}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass


async def cb_order_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["awaiting_link"] = True
    await query.edit_message_text(
        "🚀 *Order Telegram Views*\n\n"
        "Send your *Telegram post link* to boost.\n\n"
        "📋 *Format:*\n`https://t.me/channelname/123`\n\n"
        "💡 Only public channel posts are supported.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="back_menu")],
        ]),
    )


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # ── Custom amount input ──────────────────────
    if ctx.user_data.get("awaiting_custom_amount"):
        uid = update.effective_user.id
        if not spam_check(uid, 1.0):
            return
        text = update.message.text.strip()
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text(
                "❌ *Invalid amount.*\n\nPlease send a positive number only.\nExample: `250`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        amount = int(text)
        ctx.user_data.pop("awaiting_custom_amount", None)
        data = load_data()

        # Simulate a fake query-like object isn't possible here; send new message instead
        existing = get_active_payment(data, uid)
        if existing:
            p = data["payments"][existing]
            remaining = max(0, PAYMENT_EXPIRY_SEC - int(time.time() - p.get("created_ts", 0)))
            mins, secs = divmod(remaining, 60)
            save_data(data)
            await update.message.reply_text(
                f"⚠️ You already have an active payment `{existing}` (expires in {mins}m {secs}s).\n"
                f"Wait for it to expire before creating a new one.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")],
                ]),
            )
            return

        user = update.effective_user
        pid = gen_payment_id(data)
        data["payments"][pid] = {
            "user_id":    uid,
            "amount":     amount,
            "status":     "pending",
            "created_ts": time.time(),
            "created":    _now_str(),
            "username":   user.username or user.first_name,
        }
        save_data(data)
        logger.info("PAYMENT | New ID %s amount Rs%s for user %s (custom)", pid, amount, uid)

        caption = (
            f"💰 *Deposit Funds*\n\n"
            f"┌────────────────────────\n"
            f"│ 💵 Amount: ₹{amount}\n"
            f"│ 🔑 Payment ID: `{pid}`\n"
            f"│ ⏳ Expires in: *15 minutes*\n"
            f"└────────────────────────\n\n"
            f"📲 *Steps:*\n"
            f"1. Scan the QR code & pay ₹{amount} via UPI\n"
            f"2. Send: `/pay {pid} <UTR>`\n"
            f"   Example: `/pay {pid} 123456789012`\n\n"
            f"⚠️ UTR = 12+ digit number from your UPI app\n"
            f"• ID expires after 15 minutes"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")],
        ])
        try:
            if QR_IMAGE_PATH.startswith("http"):
                await update.message.reply_photo(photo=QR_IMAGE_PATH, caption=caption,
                                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            elif Path(QR_IMAGE_PATH).exists():
                with open(QR_IMAGE_PATH, "rb") as f:
                    await update.message.reply_photo(photo=f, caption=caption,
                                                      parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            else:
                raise FileNotFoundError
        except Exception:
            await update.message.reply_text(caption, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

    if not ctx.user_data.get("awaiting_link"):
        return

    # ── Custom views input ───────────────────
    if ctx.user_data.get("awaiting_custom_views"):
        uid = update.effective_user.id
        if not spam_check(uid, 1.0):
            return
        text = update.message.text.strip()
        link = ctx.user_data.get("order_link", "")
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text(
                f"❌ *Invalid input.*\n\nPlease send a whole number.\nExample: `1500`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        qty = int(text)
        if qty < VIEW_MIN:
            await update.message.reply_text(
                f"❌ Minimum views allowed: *{VIEW_MIN:,}*", parse_mode=ParseMode.MARKDOWN
            )
            return
        if qty > VIEW_MAX:
            await update.message.reply_text(
                f"❌ Maximum views allowed: *{VIEW_MAX:,}*", parse_mode=ParseMode.MARKDOWN
            )
            return
        ctx.user_data.pop("awaiting_custom_views", None)
        cost = calc_price(qty)
        data = load_data()
        bal  = get_balance(data, uid)
        if bal < cost:
            await update.message.reply_text(
                f"❌ *Insufficient Balance*\n\n"
                f"💳 Your Balance: ₹{bal}\n"
                f"💸 Required:     ₹{cost:g}\n\n"
                f"Please deposit funds first.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💰 Deposit", callback_data="deposit")],
                    [InlineKeyboardButton("🔙 Back",    callback_data="back_menu")],
                ]),
            )
            return
        await update.message.reply_text(
            f"📋 *Order Summary*\n\n"
            f"┌──────────────────────────\n"
            f"│ 🔗 Link:    `{link}`\n"
            f"│ 📊 Views:   {qty:,}\n"
            f"│ 💸 Price:   ₹{cost:g}\n"
            f"│ 💳 Balance: ₹{bal} → ₹{round(bal - cost, 2)}\n"
            f"└──────────────────────────\n\n"
            f"Confirm to place this order?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=confirm_keyboard(qty, link),
        )
        return


    uid  = update.effective_user.id
    if not spam_check(uid, 1.0):
        return

    text = update.message.text.strip()
    # Strict validation: must start with https://t.me/, valid channel name, numeric post ID
    if not text.startswith("https://t.me/"):
        await update.message.reply_text(
            "❌ *Invalid link.*\n\nLink must start with `https://t.me/`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    pattern = r"^https://t\.me/[a-zA-Z][a-zA-Z0-9_]{3,}/([1-9][0-9]*)$"
    m = re.match(pattern, text)
    if not m:
        await update.message.reply_text(
            "❌ *Invalid link format.*\n\n"
            "Accepted: `https://t.me/channelname/123`\n"
            "• Channel name ≥ 5 chars, alphanumeric\n"
            "• Post ID must be a positive number",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    ctx.user_data["awaiting_link"] = False
    ctx.user_data["order_link"]    = text

    price_lines = "\n".join(
        f"   • {q} Views — ₹{calc_price(q):g}" for q in VIEW_PRESETS
    )
    await update.message.reply_text(
        f"🔗 *Link Accepted!*\n\n`{text}`\n\n"
        f"💸 *Pricing (₹{PRICE_PER_1000}/1000 views):*\n{price_lines}\n\n"
        f"👇 Select quantity:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=qty_keyboard(text),
    )


async def cb_qty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show order summary before confirming, or ask for custom views."""
    query = update.callback_query
    await query.answer()
    _, qty_str, link = query.data.split("|", 2)

    # ── Custom views input ───────────────────
    if qty_str == "custom":
        ctx.user_data["awaiting_custom_views"] = True
        ctx.user_data["order_link"]            = link
        await query.edit_message_text(
            "✏️ *Custom Views*\n\n"
            f"Enter the number of views you want.\n\n"
            f"• Minimum: {VIEW_MIN:,}\n"
            f"• Maximum: {VIEW_MAX:,}\n"
            f"• Price: ₹{PRICE_PER_1000}/1000 views\n\n"
            "Send a number (e.g. `1500`):",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="back_menu")],
            ]),
        )
        return

    qty  = int(qty_str)
    cost = calc_price(qty)
    uid  = query.from_user.id
    data = load_data()
    bal  = get_balance(data, uid)

    if bal < cost:
        await query.edit_message_text(
            f"❌ *Insufficient Balance*\n\n"
            f"💳 Your Balance: ₹{bal}\n"
            f"💸 Required:     ₹{cost:g}\n\n"
            f"Please deposit funds first.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💰 Deposit", callback_data="deposit")],
                [InlineKeyboardButton("🔙 Back",    callback_data="back_menu")],
            ]),
        )
        return

    await query.edit_message_text(
        f"📋 *Order Summary*\n\n"
        f"┌──────────────────────────\n"
        f"│ 🔗 Link:     `{link}`\n"
        f"│ 📊 Views:    {qty:,}\n"
        f"│ 💸 Price:    ₹{cost:g}\n"
        f"│ 💳 Balance:  ₹{bal} → ₹{round(bal - cost, 2)}\n"
        f"└──────────────────────────\n\n"
        f"Confirm to place this order?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=confirm_keyboard(qty, link),
    )


async def cb_confirm_order(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ Processing...")
    _, qty_str, link = query.data.split("|", 2)
    qty  = int(qty_str)
    cost = calc_price(qty)
    user = query.from_user
    data = load_data()
    bal  = get_balance(data, user.id)

    if bal < cost:
        await query.edit_message_text(
            "❌ *Balance changed. Insufficient funds.*\n\nPlease deposit and try again.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💰 Deposit", callback_data="deposit")],
                [InlineKeyboardButton("🔙 Back",    callback_data="back_menu")],
            ]),
        )
        return

    allowed, reason = check_order_allowed(data, user.id)
    if not allowed:
        await query.edit_message_text(
            f"❌ *Order Blocked*\n\n{reason}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
            ]),
        )
        return

    # ── Duplicate link guard ────────────────
    if has_recent_order_for_link(data, user.id, link):
        await query.edit_message_text(
            "⚠️ *Duplicate Order Blocked*\n\n"
            "You already placed an order for this link in the last 24 hours.\n"
            "Please wait before re-ordering the same post.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
            ]),
        )
        return

    await query.edit_message_text(
        f"⏳ *Processing your order...*\n\n"
        f"🔗 `{link}`\n"
        f"📊 {qty:,} Views · ₹{cost:g}",
        parse_mode=ParseMode.MARKDOWN,
    )

    result = await _call_smm_api(link, qty)

    if result and ("order" in result or result.get("status") == "success"):
        deduct_balance(data, user.id, int(cost) if cost == int(cost) else cost)
        record_order(data, user.id)
        order_id = result.get("order", result.get("id", "N/A"))
        log_order(data, user.id, qty, link, order_id)
        save_data(data)
        new_bal = get_balance(data, user.id)
        logger.info("ORDER | SUCCESS user=%s qty=%s link=%s order_id=%s", user.id, qty, link, order_id)

        await query.edit_message_text(
            f"✅ *Order Placed Successfully!*\n\n"
            f"┌──────────────────────\n"
            f"│ 🆔 Order ID:  `{order_id}`\n"
            f"│ 📊 Views:     {qty:,}\n"
            f"│ 🔗 Link:      `{link}`\n"
            f"│ 💸 Charged:   ₹{cost:g}\n"
            f"│ 💳 Balance:   ₹{new_bal}\n"
            f"└──────────────────────\n\n"
            f"⚡ Views will be delivered shortly!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Main Menu", callback_data="back_menu")],
            ]),
        )
    else:
        error = (result or {}).get("error", "SMM panel unavailable. Please try again later.")
        logger.warning("ORDER | FAILED user=%s qty=%s link=%s error=%s", user.id, qty, link, error)
        await query.edit_message_text(
            f"❌ *Order Failed*\n\n"
            f"💬 Reason: `{error}`\n\n"
            f"Balance has *not* been deducted.\n"
            f"Please try again or contact admin.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📞 Contact Admin", url=CONTACT_LINK)],
                [InlineKeyboardButton("🔙 Back",          callback_data="back_menu")],
            ]),
        )


async def _call_smm_api(link: str, qty: int, attempts: int = 2) -> dict | None:
    payload = {
        "key":      API_KEY,
        "action":   "add",
        "service":  SERVICE_ID,
        "link":     link,
        "quantity": qty,
    }
    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(API_URL, data=payload)
            return resp.json()
        except Exception as e:
            logger.error("SMM API | attempt %d/%d FAILED | qty=%s link=%s | error: %s",
                         attempt, attempts, qty, link, e)
            if attempt < attempts:
                await asyncio.sleep(2)
    logger.error("SMM API | all %d attempts exhausted | qty=%s link=%s", attempts, qty, link)
    return None


# ══════════════════════════════════════════════
#  ADMIN COMMANDS
# ══════════════════════════════════════════════

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = ctx.args

    if not args or args[0] != ADMIN_PASSWORD:
        await update.message.reply_text("❌ Wrong password.")
        return

    data = load_data()
    ctx.bot_data.setdefault("admin_ids", load_admin_ids(data))
    ctx.bot_data["admin_ids"].add(user.id)
    save_admin_ids(data, ctx.bot_data["admin_ids"])
    save_data(data)

    all_pmts    = data["payments"]
    pending     = sum(1 for p in all_pmts.values() if p["status"] == "pending_confirmation")
    approved    = sum(1 for p in all_pmts.values() if p["status"] == "approved")
    revenue     = total_revenue(data)

    await update.message.reply_text(
        f"🔐 *ADMIN DASHBOARD*\n\n"
        f"┌──────────────────────────\n"
        f"│ 👥 Total Users:       {len(data['users'])}\n"
        f"│ ⏳ Pending Deposits:  {pending}\n"
        f"│ ✅ Approved Deposits: {approved}\n"
        f"│ 💰 Total Revenue:     Rs{revenue}\n"
        f"└──────────────────────────\n\n"
        f"📋 *Commands:*\n"
        f"`/approve <id> <amount>` — Approve deposit\n"
        f"`/reject <id>` — Reject deposit\n"
        f"`/listpending` — Pending payments\n"
        f"`/stats` — Detailed statistics\n"
        f"`/logs` — Last 5 payment events\n"
        f"`/setbal <uid> <amount>` — Set balance\n"
        f"`/getbal <uid>` — Check balance",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(ctx, update.effective_user.id):
        await update.message.reply_text("❌ Not authorized.")
        return

    data     = load_data()
    all_pmts = data["payments"]
    pending  = sum(1 for p in all_pmts.values() if p["status"] == "pending_confirmation")
    approved = sum(1 for p in all_pmts.values() if p["status"] == "approved")
    rejected = sum(1 for p in all_pmts.values() if p["status"] == "rejected")
    expired  = sum(1 for p in all_pmts.values() if p["status"] == "expired")
    revenue  = total_revenue(data)
    today    = _today_str()
    orders_today = sum(
        u.get("orders_today", 0)
        for u in data["users"].values()
        if u.get("last_order_date") == today
    )

    orders_total = len(data.get("orders_log", []))

    await update.message.reply_text(
        f"📊 *Detailed Statistics*\n\n"
        f"┌──────────────────────────\n"
        f"│ 👥 Total Users:       {len(data['users'])}\n"
        f"│ 💰 Total Revenue:     Rs{revenue}\n"
        f"│ 📦 Orders Today:      {orders_today}\n"
        f"│ 📊 Orders (logged):   {orders_total}\n"
        f"├──────────────────────────\n"
        f"│ 💳 Payment Summary\n"
        f"│   ⏳ Pending:         {pending}\n"
        f"│   ✅ Approved:        {approved}\n"
        f"│   ❌ Rejected:        {rejected}\n"
        f"│   ⌛ Expired:         {expired}\n"
        f"└──────────────────────────",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(ctx, update.effective_user.id):
        await update.message.reply_text("❌ Not authorized.")
        return

    data = load_data()
    logs = data.get("logs", [])[-5:]

    if not logs:
        await update.message.reply_text("📋 No payment logs yet.")
        return

    lines = ["📋 *Last 5 Payment Events:*\n"]
    for e in reversed(logs):
        lines.append(f"• `{e['ts']}` — {e['msg']}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(ctx, update.effective_user.id):
        await update.message.reply_text("❌ Not authorized.")
        return

    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: `/approve <payment_id> <amount>`", parse_mode=ParseMode.MARKDOWN
        )
        return

    pid = args[0].strip().upper()
    try:
        amount = int(float(args[1]))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Must be a positive integer.")
        return

    data = load_data()
    if pid not in data["payments"]:
        await update.message.reply_text(f"❌ Payment `{pid}` not found.", parse_mode=ParseMode.MARKDOWN)
        return

    pmt = data["payments"][pid]
    if pmt["status"] == "approved":
        await update.message.reply_text("ℹ️ Already approved.")
        return

    uid = pmt["user_id"]
    add_balance(data, uid, amount)
    data["payments"][pid].update({
        "status":      "approved",
        "amount":      amount,
        "approved_by": update.effective_user.id,
        "approved_at": _now_str(),
    })
    push_log(data, f"APPROVED | {pid} | Rs{amount} | user {uid}")
    save_data(data)
    new_bal = get_balance(data, uid)
    logger.info("PAYMENT | Approved %s Rs%s for user %s", pid, amount, uid)

    utr_display = data["payments"][pid].get("utr", "N/A")
    await update.message.reply_text(
        f"✅ *Payment Approved!*\n\n"
        f"🔑 ID: `{pid}`\n"
        f"🏦 UTR: `{utr_display}`\n"
        f"💰 Credited: Rs{amount}\n"
        f"👤 User: `{uid}`\n💳 New Balance: Rs{new_bal}",
        parse_mode=ParseMode.MARKDOWN,
    )
    try:
        await ctx.bot.send_message(
            uid,
            f"🎉 *Payment Approved!*\n\n"
            f"🔑 ID: `{pid}`\n💰 Added: Rs{amount}\n💳 Balance: Rs{new_bal}\n\n"
            f"You can now place orders! 🚀",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass


async def cmd_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(ctx, update.effective_user.id):
        await update.message.reply_text("❌ Not authorized.")
        return

    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: `/reject <payment_id>`", parse_mode=ParseMode.MARKDOWN)
        return

    pid  = args[0].strip().upper()
    data = load_data()

    if pid not in data["payments"]:
        await update.message.reply_text(f"❌ Payment `{pid}` not found.", parse_mode=ParseMode.MARKDOWN)
        return

    uid = data["payments"][pid]["user_id"]
    data["payments"][pid].update({"status": "rejected", "rejected_at": _now_str()})
    push_log(data, f"REJECTED | {pid} | user {uid}")
    save_data(data)
    logger.info("PAYMENT | Rejected %s for user %s", pid, uid)

    await update.message.reply_text(f"✅ Payment `{pid}` rejected.", parse_mode=ParseMode.MARKDOWN)
    try:
        await ctx.bot.send_message(
            uid,
            f"❌ *Payment Rejected*\n\n🔑 ID: `{pid}`\n\n"
            f"If this is a mistake, contact admin: {CONTACT_LINK}",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass


async def cmd_listpending(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(ctx, update.effective_user.id):
        await update.message.reply_text("❌ Not authorized.")
        return

    data    = load_data()
    pending = {pid: p for pid, p in data["payments"].items()
               if p["status"] == "pending_confirmation"}

    if not pending:
        await update.message.reply_text("✅ No pending payments.")
        return

    lines = [f"⏳ *Pending Payments ({len(pending)}):*\n"]
    for pid, p in pending.items():
        uname = p.get("username", "Unknown")
        utr   = p.get("utr", "?")
        lines.append(f"• `{pid}` | UTR:`{utr}` — {uname} (`{p['user_id']}`)") 

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_setbal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(ctx, update.effective_user.id):
        return
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("Usage: `/setbal <user_id> <amount>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid    = int(args[0])
        amount = max(0, int(float(args[1])))
    except ValueError:
        await update.message.reply_text("❌ Invalid input.")
        return
    data = load_data()
    set_balance(data, uid, amount)
    save_data(data)
    await update.message.reply_text(f"✅ Balance for `{uid}` set to Rs{amount}.", parse_mode=ParseMode.MARKDOWN)


async def cmd_getbal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(ctx, update.effective_user.id):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: `/getbal <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    data = load_data()
    bal  = get_balance(data, uid)
    await update.message.reply_text(f"💳 User `{uid}` balance: Rs{bal}", parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════════
#  AUTO VIEWS FEATURE
# ══════════════════════════════════════════════

async def cmd_start_auto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin-only: enable auto views on new channel posts."""
    user = update.effective_user
    data = load_data()
    if user.id not in load_admin_ids(data):
        await update.message.reply_text("❌ Admin only.")
        return
    data["auto_views"] = True
    save_data(data)
    logger.info("AUTO_VIEWS | Enabled by admin %s", user.id)
    await update.message.reply_text(
        "✅ *Auto Views ENABLED*\n\n"
        f"Every new post in the channel will automatically receive *{AUTO_VIEWS_QTY} views*.\n"
        "Use /stop_auto to disable.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_stop_auto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin-only: disable auto views."""
    user = update.effective_user
    data = load_data()
    if user.id not in load_admin_ids(data):
        await update.message.reply_text("❌ Admin only.")
        return
    data["auto_views"] = False
    save_data(data)
    logger.info("AUTO_VIEWS | Disabled by admin %s", user.id)
    await update.message.reply_text(
        "🛑 *Auto Views DISABLED*\n\nNew posts will no longer receive automatic views.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_channel_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Detect new channel posts and auto-order views if feature is enabled."""
    post = update.channel_post
    if not post:
        return

    data = load_data()

    # Feature disabled — do nothing
    if not data.get("auto_views", False):
        return

    msg_id   = post.message_id
    chat     = post.chat

    # ── Duplicate guard ──────────────────────
    processed = data.get("auto_views_log", [])
    key = f"{chat.id}:{msg_id}"
    if key in processed:
        logger.info("AUTO_VIEWS | Duplicate skipped: %s", key)
        return

    # Build post link
    username = chat.username
    if username:
        link = f"https://t.me/{username}/{msg_id}"
    else:
        # Private channel: use numeric ID form
        cid = str(chat.id).lstrip("-100")
        link = f"https://t.me/c/{cid}/{msg_id}"

    logger.info("AUTO_VIEWS | New post detected: %s  Ordering %d views", link, AUTO_VIEWS_QTY)

    # ── Place SMM order (1 retry) ────────────
    result = await _call_smm_api(link, AUTO_VIEWS_QTY)
    if not result or ("order" not in result and result.get("status") != "success"):
        logger.warning("AUTO_VIEWS | First attempt failed for %s — retrying…", link)
        await asyncio.sleep(3)
        result = await _call_smm_api(link, AUTO_VIEWS_QTY)

    # ── Mark as processed regardless of outcome ─
    processed.append(key)
    data["auto_views_log"] = processed[-500:]   # keep last 500 entries
    save_data(data)

    # ── Notify admins ────────────────────────
    admin_ids = ctx.bot_data.get("admin_ids", load_admin_ids(data))
    if result and ("order" in result or result.get("status") == "success"):
        order_id = result.get("order", result.get("id", "N/A"))
        log_msg  = (
            f"⚡ *Auto Views Triggered*\n\n"
            f"📢 New post detected\n"
            f"🔗 `{link}`\n"
            f"📊 {AUTO_VIEWS_QTY} views order placed\n"
            f"🆔 Order ID: `{order_id}`"
        )
        logger.info("AUTO_VIEWS | Success order_id=%s link=%s", order_id, link)
    else:
        error = (result or {}).get("error", "Unknown API error")
        log_msg = (
            f"❌ *Auto Views FAILED*\n\n"
            f"🔗 `{link}`\n"
            f"💬 Error: `{error}`"
        )
        logger.error("AUTO_VIEWS | FAILED link=%s error=%s", link, error)

    for aid in list(admin_ids):
        try:
            await ctx.bot.send_message(aid, log_msg, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass


# ══════════════════════════════════════════════
#  ERROR HANDLER
# ══════════════════════════════════════════════

async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception", exc_info=ctx.error)


# ══════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════

def main():
    request = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=30,
        write_timeout=30,
        connect_timeout=15,
        pool_timeout=10,
    )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .build()
    )

    data = load_data()
    removed = cleanup_old_payments(data)
    if removed:
        save_data(data)
        logger.info("STARTUP | Cleaned up %d old expired/rejected payments", removed)
    app.bot_data["admin_ids"] = load_admin_ids(data)

    # ── Commands ──────────────────────────────
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("pay",         cmd_pay))
    app.add_handler(CommandHandler("admin",       cmd_admin))
    app.add_handler(CommandHandler("approve",     cmd_approve))
    app.add_handler(CommandHandler("reject",      cmd_reject))
    app.add_handler(CommandHandler("listpending", cmd_listpending))
    app.add_handler(CommandHandler("stats",       cmd_stats))
    app.add_handler(CommandHandler("logs",        cmd_logs))
    app.add_handler(CommandHandler("setbal",      cmd_setbal))
    app.add_handler(CommandHandler("getbal",      cmd_getbal))
    app.add_handler(CommandHandler("start_auto",  cmd_start_auto))
    app.add_handler(CommandHandler("stop_auto",   cmd_stop_auto))

    # ── Callbacks ─────────────────────────────
    app.add_handler(CallbackQueryHandler(cb_check_join,    pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(cb_back_menu,     pattern="^back_menu$"))
    app.add_handler(CallbackQueryHandler(cb_balance,       pattern="^balance$"))
    app.add_handler(CallbackQueryHandler(cb_howto,         pattern="^howto$"))
    app.add_handler(CallbackQueryHandler(cb_deposit,        pattern="^deposit$"))
    app.add_handler(CallbackQueryHandler(cb_deposit_amount, pattern=r"^dep_amt\|"))
    app.add_handler(CallbackQueryHandler(cb_order_start,   pattern="^order_start$"))
    app.add_handler(CallbackQueryHandler(cb_qty,           pattern=r"^qty\|"))
    app.add_handler(CallbackQueryHandler(cb_confirm_order, pattern=r"^confirm\|"))

    # ── Free text ─────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # ── Channel posts (auto views) ─────────────
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POSTS, handle_channel_post))

    app.add_error_handler(error_handler)

    logger.info("⚡ ASHU PANEL BOT v4.0 started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()