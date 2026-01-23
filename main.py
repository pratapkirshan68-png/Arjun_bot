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

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO)

# ================= CONFIG =================
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
MONGO_URL = os.environ["MONGO_URL"]

STORAGE_CHANNEL = int(os.environ["STORAGE_CHANNEL"])
SEARCH_CHAT = int(os.environ["SEARCH_CHAT"])
FSUB_CHANNEL = int(os.environ["FSUB_CHANNEL"])

MAIN_CHANNEL_LINK = os.environ["MAIN_CHANNEL_LINK"]
SEARCH_GROUP_LINK = os.environ.get("SEARCH_GROUP_LINK", MAIN_CHANNEL_LINK)

SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN")
SHORT_API_KEY = os.environ.get("SHORT_API_KEY")

ADMIN_IDS = [int(x) for x in re.findall(r'-?\d+', os.environ.get("ADMIN_IDS", ""))]

SHORTLINK_ENABLED = True

# ===== WATERMARK TEXT =====
WATERMARK_TEXT = (
    "👁️ 2  [Movies 2026 - Cinema Pratap ❤️🌹]\n\n"
    "⚠️ Ye file 2 minute me auto delete ho jayegi!"
)

# ================= DB =================
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["PratapCinemaBot"]
movies = db["movies"]

# ================= WEB (Render Fix) =================
async def health(_):
    return web.Response(text="Bot is alive")

async def start_web():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

# ================= BOT =================
bot = Client("pratap_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ================= HELPERS =================
def clean_name(text):
    text = text.lower()
    junk = ['1080p','720p','480p','x264','x265','hevc','hindi','english']
    for j in junk:
        text = text.replace(j, '')
    return " ".join(text.replace('.', ' ').replace('_',' ').split())

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

async def auto_delete(msg, t):
    await asyncio.sleep(t)
    try:
        await msg.delete()
    except:
        pass

# ================= STORAGE ADD =================
@bot.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_movie(_, msg):
    file = msg.video or msg.document
    title = clean_name(msg.caption or file.file_name or "unknown")
    await movies.insert_one({"title": title, "file_id": file.file_id})
    await msg.reply(f"✅ Movie Database me add ho gayi:\n`{title}`")

# ================= SEARCH (1 MIN DELETE) =================
@bot.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command)
async def search(_, msg):
    query = clean_name(msg.text)
    if len(query) < 3:
        return

    # User ka search message 1 min me delete
    asyncio.create_task(auto_delete(msg, 60))

    res = await movies.find_one({"title": {"$regex": query, "$options": "i"}})
    if not res:
        sent = await msg.reply(f"❌ Bhai **{msg.from_user.first_name}**, ye movie nahi mili.")
        asyncio.create_task(auto_delete(sent, 60))
        return

    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=file_{res['_id']}"
    link = await get_shortlink(link)

    btn = InlineKeyboardMarkup([[InlineKeyboardButton("🎬 GET MOVIE", url=link)]])

    sent = await msg.reply(
        f"🎥 **Movie Mil Gayi!**\n\n👤 User: {msg.from_user.mention}\n🎬 Movie: `{res['title']}`\n\n👇 Niche click karo download ke liye",
        reply_markup=btn
    )
    # Bot ka reply bhi 1 min me delete
    asyncio.create_task(auto_delete(sent, 60))

# ================= START & FSUB (2 MIN DELETE) =================
@bot.on_message(filters.private & filters.command("start"))
async def start(_, msg):
    user_id = msg.from_user.id
    
    # 1. Force Join Check
    try:
        await bot.get_chat_member(FSUB_CHANNEL, user_id)
    except UserNotParticipant:
        start_arg = msg.command[1] if len(msg.command) > 1 else ""
        try_again_url = f"https://t.me/{bot.me.username}?start={start_arg}" if start_arg else MAIN_CHANNEL_LINK
        
        return await msg.reply(
            "❌ **Ruko Bhai!**\n\nPehle hamara channel join karo tabhi movie milegi.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 JOIN CHANNEL", url=MAIN_CHANNEL_LINK)],
                [InlineKeyboardButton("🔄 TRY AGAIN / MOVIE LE", url=try_again_url)]
            ])
        )

    # 2. Movie Dena
    if len(msg.command) > 1 and msg.command[1].startswith("file_"):
        mid = msg.command[1].replace("file_", "")
        try:
            res = await movies.find_one({"_id": ObjectId(mid)})
        except:
            res = None
            
        if not res:
            return await msg.reply("❌ Ye file database se delete ho chuki hai.")

        sent_file = await bot.send_cached_media(
            msg.chat.id,
            res["file_id"],
            caption=WATERMARK_TEXT
        )
        # File 2 min me delete
        asyncio.create_task(auto_delete(sent_file, 120))
        
        info = await msg.reply("☝️ Ye file 2 minute me delete ho jayegi, jaldi save ya forward kar lo!")
        asyncio.create_task(auto_delete(info, 120))

    else:
        await msg.reply("👋 Bhai bot ready hai! Group me jaakar search karo.")

# ================= ADMIN COMMANDS =================

@bot.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats(_, msg):
    c = await movies.count_documents({})
    await msg.reply(f"📊 **Total Movies:** {c}")

@bot.on_message(filters.command("shortn") & filters.user(ADMIN_IDS))
async def short_toggle(_, msg):
    global SHORTLINK_ENABLED
    if len(msg.command) < 2:
        return await msg.reply("Use: `/shortn on` ya `/shortn off`")
    
    state = msg.command[1].lower()
    SHORTLINK_ENABLED = (state == "on")
    await msg.reply(f"✅ Shortener ab **{state.upper()}** hai.")

@bot.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete(_, msg):
    if len(msg.command) < 2:
        return await msg.reply("❌ Movie ka naam likho. Example: `/del Pathaan`")
    q = " ".join(msg.command[1:])
    r = await movies.delete_many({"title": {"$regex": q, "$options": "i"}})
    await msg.reply(f"🗑 **{r.deleted_count}** files delete kar di gayi hain.")

# ================= RUN =================
async def main():
    await start_web()
    await bot.start()
    await idle()

if __name__ == "__main__":
    asyncio.run(main())
