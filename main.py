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
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
MONGO_URL = os.environ["MONGO_URL"]

STORAGE_CHANNEL = int(os.environ["STORAGE_CHANNEL"])
SEARCH_CHAT = int(os.environ["SEARCH_CHAT"])
FSUB_CHANNEL = int(os.environ["FSUB_CHANNEL"])

MAIN_CHANNEL_LINK = os.environ["MAIN_CHANNEL_LINK"]
SEARCH_GROUP_LINK = os.environ.get("SEARCH_GROUP_LINK", MAIN_CHANNEL_LINK)

SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN", "")
SHORT_API_KEY = os.environ.get("SHORT_API_KEY", "")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")

raw_admins = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS = [int(x) for x in re.findall(r"-?\d+", raw_admins)]

SHORTLINK_ENABLED = True

WATERMARK = (
    "🎬 Movies 2026 - Cinema Pratap ❤️🌹\n"
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
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

# ================== HELPERS ==================
def clean_name(text):
    text = text.lower()
    junk = ["1080p","720p","480p","x264","x265","hevc","hindi","english"]
    for j in junk:
        text = text.replace(j, "")
    return " ".join(text.replace(".", " ").replace("_"," ").split())

async def auto_delete(msg, t=120):
    await asyncio.sleep(t)
    try:
        await msg.delete()
    except:
        pass

async def shortlink(url):
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

async def tmdb_data(query):
    if not TMDB_API_KEY:
        return None
    url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}"
    async with aiohttp.ClientSession() as s:
        async with s.get(url) as r:
            d = await r.json()
            if d.get("results"):
                p = d["results"][0].get("poster_path")
                if p:
                    return f"https://image.tmdb.org/t/p/w300{p}"
    return None

# ================== ADD MOVIE ==================
@bot.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_movie(_, m):
    f = m.video or m.document
    title = clean_name(m.caption or f.file_name or "movie")
    await movies.insert_one({"title": title, "file_id": f.file_id})
    await m.reply(f"✅ Added: `{title}`")

# ================== SEARCH (GROUP ONLY) ==================
@bot.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.regex("^/"))
async def search(_, m):
    q = clean_name(m.text)
    if len(q) < 3:
        return

    r = await movies.find_one({"title": {"$regex": q, "$options": "i"}})
    if not r:
        return await m.reply("❌ Movie nahi mili")

    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=file_{r['_id']}"
    link = await shortlink(link)

    poster = await tmdb_data(q)

    cap = (
        f"🎬 **{r['title']}**\n\n"
        f"👤 User: {m.from_user.first_name}\n"
        f"🆔 ID: `{m.from_user.id}`"
    )

    btn = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎬 GET MOVIE", url=link)]]
    )

    if poster:
        await m.reply_photo(poster, caption=cap, reply_markup=btn)
    else:
        await m.reply(cap, reply_markup=btn)

# ================== WRONG PLACE SEARCH ==================
@bot.on_message(filters.private & filters.text & ~filters.command)
async def wrong(_, m):
    await m.reply(
        "❌ Yahan search nahi hota bhai 🙏\n"
        "👇 Niche diye gaye group me search karo",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔍 SEARCH GROUP", url=SEARCH_GROUP_LINK)]]
        )
    )

# ================== START ==================
@bot.on_message(filters.private & filters.command("start"))
async def start(_, m):
    try:
        await bot.get_chat_member(FSUB_CHANNEL, m.from_user.id)
    except UserNotParticipant:
        return await m.reply(
            "❌ Pehle channel join karo",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("JOIN", url=MAIN_CHANNEL_LINK)]]
            )
        )

    if len(m.command) > 1 and m.command[1].startswith("file_"):
        mid = m.command[1].replace("file_","")
        r = await movies.find_one({"_id": ObjectId(mid)})
        if not r:
            return await m.reply("❌ File nahi mili")

        sent = await bot.send_cached_media(
            m.chat.id,
            r["file_id"],
            caption=WATERMARK
        )
        asyncio.create_task(auto_delete(sent))
    else:
        await m.reply("✅ Bot ready, group me movie search karo")

# ================== ADMIN ==================
@bot.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats(_, m):
    c = await movies.count_documents({})
    await m.reply(f"📊 Total Movies: {c}")

@bot.on_message(filters.command("shortnr") & filters.user(ADMIN_IDS))
async def short_toggle(_, m):
    global SHORTLINK_ENABLED
    if len(m.command) < 2:
        return await m.reply("/shortnr on | off")
    SHORTLINK_ENABLED = m.command[1] == "on"
    await m.reply("✅ Done")

@bot.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete(_, m):
    q = " ".join(m.command[1:])
    r = await movies.delete_many({"title": {"$regex": q, "$options": "i"}})
    await m.reply(f"🗑 Deleted: {r.deleted_count}")

# ================== RUN ==================
async def main():
    await start_web()
    await bot.start()
    await idle()

if __name__ == "__main__":
    asyncio.run(main())
