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
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URL = os.environ.get("MONGO_URL")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")

STORAGE_CHANNEL = int(os.environ.get("STORAGE_CHANNEL"))
SEARCH_CHAT = int(os.environ.get("SEARCH_CHAT"))
FSUB_CHANNEL = int(os.environ.get("FSUB_CHANNEL"))
MAIN_CHANNEL_LINK = os.environ.get("MAIN_CHANNEL_LINK")

SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN")
SHORT_API_KEY = os.environ.get("SHORT_API_KEY")

raw_admins = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS = [int(x) for x in re.findall(r'-?\d+', raw_admins)]

SHORTLINK_ENABLED = True

# ================= DATABASE =================
mongo = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
db = mongo["PratapCinemaBot"]
movies = db["movies"]

# ================= WEB SERVER =================
async def health(request):
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
    if not text:
        return ""
    text = text.lower()
    junk = ['1080p','720p','480p','x264','x265','hevc','hindi','english']
    for j in junk:
        text = re.sub(j, '', text)
    return " ".join(text.replace('.', ' ').replace('_', ' ').split()).strip()

async def get_tmdb(query):
    if not TMDB_API_KEY:
        return None, "N/A", "0000"
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={query}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=5) as r:
                data = await r.json()
                if data.get("results"):
                    res = data["results"][0]
                    p = res.get("backdrop_path") or res.get("poster_path")
                    poster = f"https://image.tmdb.org/t/p/w500{p}" if p else None
                    rating = res.get("vote_average", "N/A")
                    year = (res.get("release_date") or res.get("first_air_date") or "0000")[:4]
                    return poster, rating, year
    except:
        pass
    return None, "N/A", "0000"

async def shortlink(url):
    if not SHORTLINK_ENABLED:
        return url
    try:
        api = f"https://{SHORT_DOMAIN}/api?api={SHORT_API_KEY}&url={url}"
        async with aiohttp.ClientSession() as s:
            async with s.get(api, timeout=10) as r:
                j = await r.json()
                if j.get("status") == "success":
                    return j.get("shortenedUrl")
    except:
        pass
    return url

async def auto_delete(msg, t=120):
    await asyncio.sleep(t)
    try:
        await msg.delete()
    except:
        pass

# ================= STORAGE ADD =================
@bot.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_movie(_, msg):
    file = msg.video or msg.document
    title = clean_name(msg.caption or file.file_name)
    await movies.insert_one({"title": title, "file_id": file.file_id})
    await msg.reply_text(f"✅ **Movie Added:** `{title}`")

# ================= SEARCH (FIXED) =================
@bot.on_message(filters.chat(SEARCH_CHAT) & filters.text)
async def search(_, msg):
    if msg.text.startswith("/"):
        return

    query = clean_name(msg.text)
    if len(query) < 3:
        return

    res = await movies.find_one({"title": {"$regex": query, "$options": "i"}})
    if not res:
        m = await msg.reply(f"❌ `{msg.text}` nahi mili.")
        asyncio.create_task(auto_delete(m, 10))
        return

    poster, rating, year = await get_tmdb(query)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=file_{res['_id']}"
    final = await shortlink(link)

    # 🔥 SAFE USER FIX (anonymous / channel)
    user = msg.from_user.mention if msg.from_user else "User"

    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 GET MOVIE", url=final)],
        [InlineKeyboardButton("📢 JOIN CHANNEL", url=MAIN_CHANNEL_LINK)]
    ])

    cap = (
        f"🎬 **Title:** `{res['title']}`\n"
        f"⭐ Rating: `{rating}` | 📅 Year: `{year}`\n\n"
        f"👤 Requested by: {user}"
    )

    if poster:
        sent = await msg.reply_photo(poster, caption=cap, reply_markup=btn)
    else:
        sent = await msg.reply(cap, reply_markup=btn)

    asyncio.create_task(auto_delete(sent, 120))

# ================= START =================
@bot.on_message(filters.command("start") & filters.private)
async def start(_, msg):
    try:
        await bot.get_chat_member(FSUB_CHANNEL, msg.from_user.id)
    except UserNotParticipant:
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("📢 JOIN CHANNEL", url=MAIN_CHANNEL_LINK)]])
        return await msg.reply("❌ Pehle channel join karo!", reply_markup=btn)

    if len(msg.command) > 1 and msg.command[1].startswith("file_"):
        mid = msg.command[1].split("_")[1]
        res = await movies.find_one({"_id": ObjectId(mid)})
        if res:
            sent = await bot.send_cached_media(
                msg.chat.id,
                res["file_id"],
                caption=f"🎥 **{res['title']}**\n\n⚠️ Auto delete in 2 mins"
            )
            asyncio.create_task(auto_delete(sent, 120))
    else:
        await msg.reply("👋 Movie search group me karo!")

# ================= RUN =================
async def main():
    await start_web()
    await bot.start()
    print("🤖 Bot Started")
    await idle()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
