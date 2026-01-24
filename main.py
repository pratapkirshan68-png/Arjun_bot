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
logger = logging.getLogger(__name__)

# ================= CONFIG =================
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URL = os.environ.get("MONGO_URL")

STORAGE_CHANNEL = int(os.environ.get("STORAGE_CHANNEL"))
SEARCH_CHAT = int(os.environ.get("SEARCH_CHAT"))
FSUB_CHANNEL = int(os.environ.get("FSUB_CHANNEL"))

MAIN_CHANNEL_LINK = os.environ.get("MAIN_CHANNEL_LINK")
SEARCH_GROUP_LINK = os.environ.get("SEARCH_GROUP_LINK", MAIN_CHANNEL_LINK)

SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN")
SHORT_API_KEY = os.environ.get("SHORT_API_KEY")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")

# Admin IDs
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

# ================= WEB (Render Health Check) =================
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
bot = Client(
    "pratap_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ================= HELPERS =================
def clean_name(text):
    if not text:
        return "unknown"
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
                if r.status == 200:
                    j = await r.json()
                    return j.get("shortenedUrl", url)
    except Exception as e:
        logger.error(f"Shortener Error: {e}")
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
# ERROR WAS HERE: Replaced ~filters.command with ~filters.regex("^/")
@bot.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.regex(r"^/"))
async def search(_, msg):
    query = clean_name(msg.text)
    if len(query) < 3:
        return

    res = await movies.find_one({"title": {"$regex": query, "$options": "i"}})
    if not res:
        temp_msg = await msg.reply("❌ Movie nahi mili")
        await asyncio.sleep(5)
        try: await temp_msg.delete()
        except: pass
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

# ================= START & FILE SENDING =================
@bot.on_message(filters.private & filters.command("start"))
async def start(_, msg):
    # 1. Check Subscription
    try:
        await bot.get_chat_member(FSUB_CHANNEL, msg.from_user.id)
    except UserNotParticipant:
        start_arg = msg.command[1] if len(msg.command) > 1 else ""
        invite_link = MAIN_CHANNEL_LINK or ""
        
        try_again_url = f"https://t.me/{bot.me.username}?start={start_arg}" if start_arg else invite_link

        return await msg.reply(
            "❌ Pehle channel join karo fir movie milegi!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("JOIN CHANNEL", url=invite_link)],
                [InlineKeyboardButton("TRY AGAIN / MOVIE LE", url=try_again_url)]
            ])
        )
    except Exception as e:
        logger.error(f"Fsub Error: {e}")

    # 2. Give Movie File
    if len(msg.command) > 1 and msg.command[1].startswith("file_"):
        mid = msg.command[1].replace("file_", "")
        try:
            res = await movies.find_one({"_id": ObjectId(mid)})
        except:
            res = None
            
        if not res:
            return await msg.reply("❌ File nahi mili ya delete ho gayi.")

        sent = await bot.send_cached_media(
            msg.chat.id,
            res["file_id"],
            caption=WATERMARK_TEXT
        )
        asyncio.create_task(auto_delete(sent))

    else:
        await msg.reply("👋 Bot Ready! Group me jakar movie search karo.")

# ================= WRONG PLACE SEARCH (PM) =================
# ERROR WAS HERE TOO: Replaced ~filters.command with ~filters.regex("^/")
@bot.on_message(filters.private & filters.text & ~filters.regex(r"^/"))
async def wrong_place(_, msg):
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 SEARCH GROUP", url=SEARCH_GROUP_LINK)]
    ])
    await msg.reply(
        "❌ Yahan search nahi hota bhai 🙏👍\n\n"
        "👉 Movie search karne ke liye niche diye gaye group par jao.",
        reply_markup=btn
    )

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
    if state == "on":
        SHORTLINK_ENABLED = True
        await msg.reply("✅ Shortener **ON** kar diya.")
    elif state == "off":
        SHORTLINK_ENABLED = False
        await msg.reply("❌ Shortener **OFF** kar diya.")
    else:
        await msg.reply("❌ Sahi command likho: on ya off")

@bot.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete(_, msg):
    if len(msg.command) < 2:
        return await msg.reply("❌ Movie ka naam likho delete karne ke liye.")
        
    q = " ".join(msg.command[1:])
    r = await movies.delete_many({"title": {"$regex": q, "$options": "i"}})
    await msg.reply(f"🗑 **Deleted:** {r.deleted_count} files.")

# ================= RUN =================
async def main():
    await start_web()
    logger.info("Bot Starting...")
    await bot.start()
    logger.info("Bot Started!")
    await idle()
    await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
