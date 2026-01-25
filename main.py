import os
import re
import asyncio
import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant
from bson.objectid import ObjectId
from aiohttp import web

# ================== CONFIG ==================
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URL = os.environ.get("MONGO_URL")

STORAGE_CHANNEL = int(os.environ.get("STORAGE_CHANNEL"))
SEARCH_CHAT = int(os.environ.get("SEARCH_CHAT"))
FSUB_CHANNEL = int(os.environ.get("FSUB_CHANNEL"))

MAIN_CHANNEL_LINK = os.environ.get("MAIN_CHANNEL_LINK")
SEARCH_GROUP_LINK = os.environ.get("SEARCH_GROUP_LINK", MAIN_CHANNEL_LINK)

TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN", "")
SHORT_API_KEY = os.environ.get("SHORT_API_KEY", "")

# ADMIN IDS
ADMIN_IDS = [int(x) for x in re.findall(r"\d+", os.environ.get("ADMIN_IDS", ""))]

SHORTLINK_ENABLED = True

WATERMARK = (
    "🎬 Movies 2026 - Cinema Pratap ❤️🌹\n\n"
    "⚠️ Ye file 2 minute me auto delete ho jayegi"
)

# ================== DB ==================
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["PratapCinemaBot"]
movies = db["movies"]

# ================== BOT ==================
bot = Client(
    "pratap_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ================== WEB (Render) ==================
async def health(_):
    return web.Response(text="Bot Alive")

async def start_web():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080))).start()

# ================== HELPERS ==================
def clean(text):
    text = text.lower()
    for x in ["1080p", "720p", "480p", "x264", "x265", "hindi", "english"]:
        text = text.replace(x, "")
    return " ".join(text.replace(".", " ").split())

async def shortlink(url):
    if not SHORTLINK_ENABLED or not SHORT_DOMAIN:
        return url
    try:
        api = f"https://{SHORT_DOMAIN}/api?api={SHORT_API_KEY}&url={url}"
        async with aiohttp.ClientSession() as s:
            async with s.get(api) as r:
                j = await r.json()
                return j.get("shortenedUrl", url)
    except:
        return url

async def tmdb(query):
    if not TMDB_API_KEY:
        return None, "N/A", "N/A"
    url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}"
    async with aiohttp.ClientSession() as s:
        async with s.get(url) as r:
            j = await r.json()
            if j.get("results"):
                m = j["results"][0]
                poster = "https://image.tmdb.org/t/p/w500" + str(m.get("poster_path"))
                return poster, m.get("vote_average"), m.get("release_date", "")[:4]
    return None, "N/A", "N/A"

async def auto_delete(msg, t=120):
    await asyncio.sleep(t)
    try:
        await msg.delete()
    except:
        pass

# ================== ADD FILE ==================
@bot.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add(_, msg):
    file = msg.video or msg.document
    title = clean(msg.caption or file.file_name)
    await movies.insert_one({"title": title, "file_id": file.file_id})
    await msg.reply("✅ Movie Added")

# ================== SEARCH (GROUP ONLY) ==================
@bot.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command())
async def search(_, msg):
    q = clean(msg.text)
    if len(q) < 3:
        return

    m = await movies.find_one({"title": {"$regex": q, "$options": "i"}})
    if not m:
        return await msg.reply("😔 Movie nahi mili")

    poster, rating, year = await tmdb(q)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=file_{m['_id']}"
    link = await shortlink(link)

    cap = (
        f"🎬 **{m['title']}**\n"
        f"⭐ {rating} | 📅 {year}\n\n"
        f"👤 {msg.from_user.mention}\n"
        f"🆔 `{msg.from_user.id}`\n\n"
        f"😔 Search working..."
    )

    btn = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 GET MOVIE", url=link)]])

    if poster:
        await msg.reply_photo(poster, caption=cap, reply_markup=btn)
    else:
        await msg.reply(cap, reply_markup=btn)

# ================== WRONG PLACE ==================
@bot.on_message(filters.private & filters.text & ~filters.command())
async def wrong(_, msg):
    await msg.reply(
        "❌ Yahan search nahi hota bhai 🙏\n\nGroup me search karo",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 SEARCH GROUP", url=SEARCH_GROUP_LINK)]
        ])
    )

# ================== START ==================
@bot.on_message(filters.private & filters.command("start"))
async def start(_, msg):
    try:
        await bot.get_chat_member(FSUB_CHANNEL, msg.from_user.id)
    except UserNotParticipant:
        return await msg.reply(
            "❌ Pehle channel join karo",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("JOIN", url=MAIN_CHANNEL_LINK)]
            ])
        )

    if len(msg.command) > 1 and msg.command[1].startswith("file_"):
        mid = msg.command[1].split("_")[1]
        m = await movies.find_one({"_id": ObjectId(mid)})
        if not m:
            return await msg.reply("File nahi mili")

        sent = await bot.send_cached_media(
            msg.chat.id,
            m["file_id"],
            caption=WATERMARK
        )
        asyncio.create_task(auto_delete(sent))
    else:
        await msg.reply("👋 Bot Ready")

# ================== ADMIN ==================
@bot.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats(_, msg):
    c = await movies.count_documents({})
    await msg.reply(f"📊 Total Movies: {c}")

@bot.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete(_, msg):
    q = " ".join(msg.command[1:])
    r = await movies.delete_many({"title": {"$regex": q, "$options": "i"}})
    await msg.reply(f"🗑 Deleted: {r.deleted_count}")

@bot.on_message(filters.command("shortnr") & filters.user(ADMIN_IDS))
async def short(_, msg):
    global SHORTLINK_ENABLED
    SHORTLINK_ENABLED = msg.command[1] == "on"
    await msg.reply("✅ Done")

# ================== RUN ==================
async def main():
    await start_web()
    await bot.start()
    await idle()

asyncio.run(main())
