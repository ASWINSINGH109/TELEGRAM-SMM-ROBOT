import requests, json, os, asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ===== CONFIG =====
TOKEN = "8520728694:AAEq_DFINNKpZc_N1O2xOwN4B3lNBmm7pUw"
API_KEY = "788f5f7406b81fc61b73a0eba8d5e572883a0862"
API_URL = "https://smmvault.in/api/v2"

CHANNEL_USERNAME = "IG_LOOTERS"
CHANNEL_LINK = "https://t.me/IG_LOOTERS"
WEBSITE = "https://smmvault.in/ref/Q2WY"
ADMIN_LINK = "https://t.me/ashuh4reee"

SERVICE_ID = 1658
HEADER = "━━━━━━━━━━━━━━━\n⚡ ASHU PANEL ⚡\n━━━━━━━━━━━━━━━"

USER_FILE = "users.json"

# ===== USERS =====
def load_users():
    if not os.path.exists(USER_FILE):
        return {}
    with open(USER_FILE, "r") as f:
        return json.load(f)

def save_users():
    with open(USER_FILE, "w") as f:
        json.dump(users, f)

users = load_users()

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
        [InlineKeyboardButton("➕ Add Me To Your Channel",
                              url=f"https://t.me/{context.bot.username}?startchannel=true")],
        [InlineKeyboardButton("📘 How To Use", callback_data="how")],
        [InlineKeyboardButton("📞 Support", url=ADMIN_LINK)]
    ]

    text = (
        f"{HEADER}\n\n"
        "🔥 *WELCOME PANEL*\n\n"
        "⚡ Fast Telegram Views\n"
        "💸 Easy Deposit System\n\n"
        "👇 Use buttons below"
    )

    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown",
                                        reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.callback_query.message.edit_text(text, parse_mode="Markdown",
                                                      reply_markup=InlineKeyboardMarkup(buttons))

# ===== START =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_screen(update, context)
    await update.message.reply_text("👇 Menu", reply_markup=menu())

# ===== BACK =====
async def back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await start_screen(update, context)

# ===== HOW TO USE =====
async def how(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    text = (
        f"{HEADER}\n\n"
        "📘 *HOW TO USE*\n\n"

        "💰 STEP 1: ADD BALANCE\n"
        "• Open website\n"
        "• Create account\n"
        "• Deposit funds\n\n"

        "🚀 STEP 2: GET VIEWS\n"
        "Option 1:\n"
        "• Add bot as admin in your channel\n"
        "• Post in your channel\n"
        "• Views will come automatically\n\n"

        "Option 2:\n"
        "• Send post link to bot\n"
        "• Views start instantly\n\n"

        "⚠️ Without balance, views will not start"
    )

    buttons = [
        [InlineKeyboardButton("🌐 Deposit Now", url=WEBSITE)],
        [InlineKeyboardButton("➕ Add Me To Your Channel",
                              url=f"https://t.me/{context.bot.username}?startchannel=true")],
        [InlineKeyboardButton("📞 Support", url=ADMIN_LINK)],
        [InlineKeyboardButton("🔙 Back", callback_data="back")]
    ]

    await q.message.edit_text(text, parse_mode="Markdown",
                              reply_markup=InlineKeyboardMarkup(buttons))

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
        await update.message.reply_text("📩 Send your Telegram post link")

    elif text == "💰 Balance":
        await update.message.reply_text(f"💰 Balance: ₹{users[user_id]}")

    elif text == "📞 Support":
        await update.message.reply_text("📞 Contact admin",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Admin", url=ADMIN_LINK)]]))

    elif "t.me" in text:
        if users[user_id] < 2:
            await update.message.reply_text("❌ Low balance")
            return

        users[user_id] -= 2
        save_users()

        msg = await update.message.reply_text("⏳ Initializing...")
        await asyncio.sleep(1)
        await msg.edit_text("⚡ Connecting...")
        await asyncio.sleep(1)

        for i in range(6):
            await asyncio.sleep(0.4)
            await msg.edit_text("🔄 Processing...")

        views = 0
        for i in range(5):
            await asyncio.sleep(0.5)
            views += 50
            await msg.edit_text(f"👁 Views Delivered: {views}")

        requests.post(API_URL, data={
            "key": API_KEY,
            "action": "add",
            "service": SERVICE_ID,
            "link": text,
            "quantity": 100
        })

        await msg.edit_text("✅ Views Started Successfully")

# ===== AUTO VIEWS =====
async def auto_views(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return

    post_id = update.channel_post.message_id
    link = f"https://t.me/{CHANNEL_USERNAME}/{post_id}"

    requests.post(API_URL, data={
        "key": API_KEY,
        "action": "add",
        "service": SERVICE_ID,
        "link": link,
        "quantity": 100
    })

# ===== RUN =====
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(how, pattern="how"))
    app.add_handler(CallbackQueryHandler(back, pattern="back"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, user_menu))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, auto_views))

    app.run_polling()

if __name__ == "__main__":
    main()