import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.request import HTTPXRequest

# ===== CONFIG =====
TOKEN = "8654195292:AAHtm-Y6O8wTBd7tRZbtDdiE-hNDgQzMab8"
API_KEY = "0e8ea1b7a669636d27156f56a15f4b5d87b0b79f"

API_URL = "https://mysmmapi.com/api/v2"
SERVICE_ID = 4807
CHANNEL_USERNAME = "IG_LOOTERS"

# ===== STATE =====
AUTO_ON = False
DONE = set()

# ===== COMMANDS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot Activated")

async def start_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_ON
    AUTO_ON = True
    await update.message.reply_text("Auto Views ON")

async def stop_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_ON
    AUTO_ON = False
    await update.message.reply_text("Auto Views OFF")

# ===== AUTO VIEW SYSTEM =====
async def auto_views(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_ON

    if not update.channel_post:
        return

    if not AUTO_ON:
        return

    post_id = str(update.channel_post.message_id)

    # duplicate block
    if post_id in DONE:
        return

    DONE.add(post_id)

    link = f"https://t.me/{CHANNEL_USERNAME}/{post_id}"

    requests.post(API_URL, data={
        "key": API_KEY,
        "action": "add",
        "service": SERVICE_ID,
        "link": link,
        "quantity": 110
    })

    print(f"Order Done for Post {post_id}")

# ===== MAIN =====
def main():
    request = HTTPXRequest(connect_timeout=60, read_timeout=60)

    app = Application.builder().token(TOKEN).request(request).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("start_orders", start_orders))
    app.add_handler(CommandHandler("stop_orders", stop_orders))

    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, auto_views))

    print("BOT RUNNING...")

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()