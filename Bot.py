import json
import asyncio
import requests

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.request import HTTPXRequest

# ===== CONFIG =====
TOKEN = "8520728694:AAEq_DFINNKpZc_N1O2xOwN4B3lNBmm7pUw"
API_KEY = "788f5f7406b81fc61b73a0eba8d5e572883a0862"

API_URL = "https://smmvault.in/api/v2"

CHANNEL_USERNAME = "IG_LOOTERS"
CHANNEL_LINK = "https://t.me/IG_LOOTERS"
WEBSITE = "https://smmvault.in/ref/Q2WY"
ADMIN_LINK = "https://t.me/ashuh4reee"

SERVICE_ID = 1658

USER_FILE = "users.json"
DONE_FILE = "done.json"

HEADER = "━━━━━━━━━━━━━━━\n⚡ ASHU PANEL ⚡\n━━━━━━━━━━━━━━━"

# ===== USER STORAGE =====
def load_users():
    try:
        return json.load(open(USER_FILE))
    except:
        return {}

def save_users():
    json.dump(users, open(USER_FILE, "w"))

users = load_users()

# ===== DONE POSTS STORAGE =====
def load_done():
    try:
        return set(json.load(open(DONE_FILE)))
    except:
        return set()

def save_done(data):
    json.dump(list(data), open(DONE_FILE, "w"))

done_posts = load_done()

# ===== MENU =====
def menu():
    return ReplyKeyboardMarkup(
        [["🏠 Home", "🚀 Get Views"],
         ["💰 Balance", "📞 Support"]],
        resize_keyboard=True
    )

# ===== START SCREEN =====
async def start_screen(update, context):
    buttons = [
        [InlineKeyboardButton("🚀 Join Channel", url=CHANNEL_LINK)],
        [InlineKeyboardButton("🌐 Deposit", url=WEBSITE)],
        [InlineKeyboardButton("➕ Add Me To Channel",
                              url=f"https://t.me/{context.bot.username}?startchannel=true")],
        [InlineKeyboardButton("📘 How To Use", callback_data="how")],
        [InlineKeyboardButton("📞 Support", url=ADMIN_LINK)]
    ]

    text = f"{HEADER}\n\nWelcome to ASHU Panel\n\nUse buttons below"

    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

# ===== COMMANDS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_screen(update, context)
    await update.message.reply_text("Menu", reply_markup=menu())

async def how(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    text = """HOW TO USE

1. Deposit balance
2. Send post link OR add bot as admin
3. Get views automatically
"""

    buttons = [
        [InlineKeyboardButton("Deposit", url=WEBSITE)],
        [InlineKeyboardButton("Back", callback_data="back")]
    ]

    await q.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

async def back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await start_screen(update, context)

# ===== USER MENU =====
async def user_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    text = update.message.text
    user_id = str(update.effective_user.id)

    if user_id not in users:
        users[user_id] = 0
        save_users()

    if text == "🏠 Home":
        await start_screen(update, context)

    elif text == "🚀 Get Views":
        await update.message.reply_text("Send Telegram post link")

    elif text == "💰 Balance":
        await update.message.reply_text(f"Balance: ₹{users[user_id]}")

    elif text == "📞 Support":
        await update.message.reply_text(
            "Contact admin",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Admin", url=ADMIN_LINK)]]
            ),
        )

    elif "t.me" in text:

        if users[user_id] < 2:
            await update.message.reply_text("Low balance")
            return

        users[user_id] -= 2
        save_users()

        msg = await update.message.reply_text("Processing...")

        await asyncio.sleep(2)

        requests.post(API_URL, data={
            "key": API_KEY,
            "action": "add",
            "service": SERVICE_ID,
            "link": text,
            "quantity": 100
        })

        await msg.edit_text("Views Started")

# ===== AUTO SAFE VIEWS =====
async def auto_views(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return

    post_id = str(update.channel_post.message_id)

    if post_id in done_posts:
        return

    done_posts.add(post_id)
    save_done(done_posts)

    link = f"https://t.me/{CHANNEL_USERNAME}/{post_id}"

    requests.post(API_URL, data={
        "key": API_KEY,
        "action": "add",
        "service": SERVICE_ID,
        "link": link,
        "quantity": 100
    })

# ===== MAIN =====
def main():
    request = HTTPXRequest(connect_timeout=60, read_timeout=60)

    app = Application.builder().token(TOKEN).request(request).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(how, pattern="how"))
    app.add_handler(CallbackQueryHandler(back, pattern="back"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, user_menu))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, auto_views))

    print("BOT STARTED")

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()