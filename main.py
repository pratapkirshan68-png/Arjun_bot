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

SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN", "")
SHORT_API_KEY = os.environ.get("SHORT_API_KEY", "")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")

ADMIN_IDS = [int(x) for x in re.findall(r'-?\d+', os.environ.get("ADMIN_IDS", ""))]

SHORTLINK_ENABLED = True

WATERMARK = (
    "👁️ 2  Movies 2026 - Cinema Pratap ❤️🌹\n\n"
    "⚠️ Ye file 2 minute me auto delete ho jayegi!"
)

# ================= DB =================
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["PratapCinemaBot"]
movies = db["movies"]

# ================= WEB (Render) =================
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
bot = Client(
    "pratap_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ================= HELPERS =================
def clean_name(text):
    text = text.lower()
    junk = ['1080p','720p','480p','x264','x265','hevc','hindi','english']
    for j in junk:
        text = text.replace(j, '')
    return " ".join(text.replace('.', ' ').replace('_',' ').split())

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
    title = clean_name(msg.caption or file.file_name or "unknown")

    await movies.insert_one({
        "title": title,
        "file_id": file.file_id
    })

    await msg.reply(f"✅ Movie Added:\n`{title}`")

# ================= SEARCH (ONLY SEARCH GROUP) =================
@bot.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start","pratap","del","shortnr"]))
async def search(_, msg):
    query = clean_name(msg.text)
    if len(query) < 3:
        return

    res = await movies.find_one({"title": {"$regex": query, "$options": "i"}})
    if not res:
        await msg.reply("❌ Movie nahi mili 😔")
        return

    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=file_{res['_id']}"
    link = await shortlink(link)

    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 GET MOVIE", url=link)]
    ])

    await msg.reply(
        f"🎥 **{res['title']}**\n\n👇 Download ke liye niche dabao",
        reply_markup=btn
    )

# ================= WRONG PLACE SEARCH =================
@bot.on_message(filters.private & filters.text)
async def wrong_place(_, msg):
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 SEARCH GROUP", url=SEARCH_GROUP_LINK)]
    ])
    await msg.reply(
        "❌ Yahan search nahi hota bhai 🙏👍\n\n"
        "👉 Movie search karne ke liye niche group par jao.",
        reply_markup=btn
    )

# ================= START =================
@bot.on_message(filters.private & filters.command("start"))
async def start(_, msg):
    try:
        await bot.get_chat_member(FSUB_CHANNEL, msg.from_user.id)
    except UserNotParticipant:
        return await msg.reply(
            "❌ Pehle channel join karo",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("JOIN CHANNEL", url=MAIN_CHANNEL_LINK)]
            ])
        )

    if len(msg.command) > 1 and msg.command[1].startswith("file_"):
        mid = msg.command[1].replace("file_","")
        res = await movies.find_one({"_id": ObjectId(mid)})
        if not res:
            return await msg.reply("❌ File nahi mili")

        sent = await bot.send_cached_media(
            msg.chat.id,
            res["file_id"],
            caption=WATERMARK
        )
        asyncio.create_task(auto_delete(sent))
    else:
        await msg.reply("👋 Bot Ready\nMovie search group me karo 🎬")

# ================= ADMIN =================
@bot.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats(_, msg):
    c = await movies.count_documents({})
    await msg.reply(f"📊 Total Movies: {c}")

@bot.on_message(filters.command("shortnr") & filters.user(ADMIN_IDS))
async def short_toggle(_, msg):
    global SHORTLINK_ENABLED
    if len(msg.command) < 2:
        return await msg.reply("Use: /shortnr on | off")
    SHORTLINK_ENABLED = msg.command[1].lower() == "on"
    await msg.reply("✅ Done")

@bot.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete(_, msg):
    q = " ".join(msg.command[1:])
    r = await movies.delete_many({"title": {"$regex": q, "$options": "i"}})
    await msg.reply(f"🗑 Deleted: {r.deleted_count}")

# ================= RUN =================
async def main():
    await start_web()
    await bot.start()
    print("🤖 Bot Started")
    await idle()

if __name__ == "__main__":
    asyncio.run(main())
