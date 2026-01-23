import os
import re
import asyncio
import aiohttp
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant
from bson.objectid import ObjectId
from aiohttp import web

# ================= CONFIG =================
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
MONGO_URL = os.environ["MONGO_URL"]

STORAGE_CHANNEL = int(os.environ["STORAGE_CHANNEL"])
SEARCH_CHAT = int(os.environ["SEARCH_CHAT"])
FSUB_CHANNEL = int(os.environ["FSUB_CHANNEL"])

MAIN_CHANNEL_LINK = os.environ["MAIN_CHANNEL_LINK"]
ADMIN_IDS = [int(x) for x in re.findall(r'-?\d+', os.environ.get("ADMIN_IDS", ""))]

SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN")
SHORT_API_KEY = os.environ.get("SHORT_API_KEY")

SHORTLINK_ENABLED = True

# ===== WATERMARK (Hidden Style) =====
# Isme zero-width spaces use kiye hain taaki thoda alag dikhe
WATERMARK_TEXT = (
    "👁️‍🗨️ [Movies 2026 - Cinema Pratap ❤️🌹]\n\n"
    "⚠️ Ye file 2 minute me auto delete ho jayegi! Kahin aur save kar lo."
)

# ================= DB =================
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["PratapCinemaBot"]
movies = db["movies"]

# ================= WEB SERVER (For Render) =================
async def health(_):
    return web.Response(text="Bot Chal Raha Hai!")

async def start_web():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

# ================= BOT CLIENT =================
bot = Client("pratap_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ================= HELPERS =================
async def auto_delete(msg, t):
    await asyncio.sleep(t)
    try:
        await msg.delete()
    except:
        pass

async def get_shortlink(url):
    if not SHORTLINK_ENABLED or not SHORT_DOMAIN:
        return url
    try:
        api = f"https://{SHORT_DOMAIN}/api?api={SHORT_API_KEY}&url={url}"
        async with aiohttp.ClientSession() as s:
            async with s.get(api, timeout=10) as r:
                j = await r.json()
                return j.get("shortenedUrl", url)
    except:
        return url

# ================= SEARCH (1 MIN AUTO DELETE) =================
@bot.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command())
async def search_handler(_, msg):
    query = msg.text.strip().lower()
    if len(query) < 3: return

    # User ka message 1 min me delete
    asyncio.create_task(auto_delete(msg, 60))

    res = await movies.find_one({"title": {"$regex": query, "$options": "i"}})
    if not res:
        err_msg = await msg.reply("❌ Sorry bhai, ye movie nahi mili.")
        asyncio.create_task(auto_delete(err_msg, 60))
        return

    me = await bot.get_me()
    start_url = f"https://t.me/{me.username}?start=file_{res['_id']}"
    short_url = await get_shortlink(start_url)

    btn = InlineKeyboardMarkup([[InlineKeyboardButton("🎬 GET MOVIE", url=short_url)]])
    
    # Bot ka reply 1 min me delete
    sent = await msg.reply(
        f"🎥 **Movie Mil Gayi!**\n\n🎬 Title: `{res['title']}`\n\n👇 Niche click karke download karo",
        reply_markup=btn
    )
    asyncio.create_task(auto_delete(sent, 60))

# ================= START & JOIN LOGIC (2 MIN DELETE) =================
@bot.on_message(filters.private & filters.command("start"))
async def start_handler(_, msg):
    user_id = msg.from_user.id
    
    # 1. Join Check
    try:
        await bot.get_chat_member(FSUB_CHANNEL, user_id)
    except UserNotParticipant:
        arg = msg.command[1] if len(msg.command) > 1 else ""
        return await msg.reply(
            "❌ **Pehle join karo!**\n\nBina join kiye movie nahi milegi bhai.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 JOIN CHANNEL", url=MAIN_CHANNEL_LINK)],
                [InlineKeyboardButton("🔄 TRY AGAIN / START", url=f"https://t.me/{bot.me.username}?start={arg}")]
            ])
        )

    # 2. File Delivery
    if len(msg.command) > 1 and msg.command[1].startswith("file_"):
        f_id = msg.command[1].replace("file_", "")
        try:
            res = await movies.find_one({"_id": ObjectId(f_id)})
        except:
            res = None

        if res:
            file_sent = await bot.send_cached_media(msg.chat.id, res["file_id"], caption=WATERMARK_TEXT)
            # File 2 minute me delete
            asyncio.create_task(auto_delete(file_sent, 120))
            
            # Delete warning message
            warn = await msg.reply("☝️ **Ye file 2 minute me delete ho jayegi!**")
            asyncio.create_task(auto_delete(warn, 120))
        else:
            await msg.reply("❌ Ye file ab database me nahi hai.")
    else:
        await msg.reply("👋 Bhai bot active hai! Movie search group me ja kar search karo.")

# ================= ADMIN COMMANDS =================

@bot.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def count_movies(_, msg):
    c = await movies.count_documents({})
    await msg.reply(f"📊 **Total Movies in DB:** {c}")

@bot.on_message(filters.command("shortn") & filters.user(ADMIN_IDS))
async def toggle_shortn(_, msg):
    global SHORTLINK_ENABLED
    if len(msg.command) < 2:
        return await msg.reply("Use: `/shortn on` or `/shortn off`")
    
    mode = msg.command[1].lower()
    SHORTLINK_ENABLED = (mode == "on")
    await msg.reply(f"✅ Shortener setting: **{SHORTLINK_ENABLED}**")

@bot.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete_movie(_, msg):
    if len(msg.command) < 2:
        return await msg.reply("❌ Movie ka naam likho delete karne ke liye.")
    
    target = " ".join(msg.command[1:])
    deleted = await movies.delete_many({"title": {"$regex": target, "$options": "i"}})
    await msg.reply(f"🗑 **{deleted.deleted_count}** files delete kar di gayi hain.")

# ================= STORAGE =================
@bot.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def save_to_db(_, msg):
    file = msg.video or msg.document
    title = (msg.caption or file.file_name or "unknown").lower()
    await movies.insert_one({"title": title, "file_id": file.file_id})
    await msg.reply(f"✅ Database me save ho gaya:\n`{title}`")

# ================= MAIN RUN =================
async def start_all():
    await start_web()
    await bot.start()
    print("Bot is online!")
    await idle()

if __name__ == "__main__":
    asyncio.run(start_all())
