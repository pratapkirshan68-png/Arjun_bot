import os
import re
import asyncio
import aiohttp
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant, FloodWait
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

MAIN_CHANNEL_LINK = os.environ.get("MAIN_CHANNEL_LINK") # Ye wahi channel hai jaha POSTAR jayega
SEARCH_GROUP_LINK = os.environ.get("SEARCH_GROUP_LINK", MAIN_CHANNEL_LINK)

SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN")
SHORT_API_KEY = os.environ.get("SHORT_API_KEY")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")

# Admin IDs ko safely nikalna
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

# ================= BOT CLIENT =================
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

async def auto_delete(msg, t=60):
    await asyncio.sleep(t)
    try:
        await msg.delete()
    except:
        pass

# ================= 1. AUTO ADD MOVIE (STORAGE) =================
@bot.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_movie(_, msg):
    file = msg.video or msg.document
    title = clean_name(msg.caption or file.file_name or "unknown")
    
    # Check duplicate
    exist = await movies.find_one({"file_id": file.file_id})
    if exist:
        sent = await msg.reply(f"❌ **{title}** pehle se added hai!")
        asyncio.create_task(auto_delete(sent, 60))
        return

    await movies.insert_one({
        "title": title,
        "file_id": file.file_id
    })
    
    # Confirmation Message (1 minute me delete hoga)
    sent = await msg.reply(f"✅ **Movie Added:**\n`{title}`\n\n_Ye message 1 min me delete ho jayega._")
    asyncio.create_task(auto_delete(sent, 60))

# ================= 2. POSTAR COMMAND (NEW) =================
# Use: Photo ke sath caption me likho: /postar MovieName
# Ya photo pe reply karke likho: /postar MovieName
@bot.on_message(filters.command("postar") & filters.user(ADMIN_IDS))
async def postar_cmd(client, msg):
    # Determine Photo and Title
    if msg.reply_to_message and msg.reply_to_message.photo:
        photo = msg.reply_to_message.photo.file_id
        # Agar command ke aage naam likha hai to wo lo, nahi to reply wala caption
        if len(msg.command) > 1:
            title = " ".join(msg.command[1:])
        else:
            title = msg.reply_to_message.caption or "New Movie"
    elif msg.photo:
        photo = msg.photo.file_id
        if len(msg.command) > 1:
            title = " ".join(msg.command[1:])
        else:
            title = msg.caption or "New Movie"
    else:
        return await msg.reply("❌ Kisi photo pe reply karo ya photo ke sath command bhejo!")

    # Button
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 SEARCH & DOWNLOAD HERE", url=SEARCH_GROUP_LINK)]
    ])

    # Caption (Hidden Watermark style - Clean)
    caption = f"🎬 **{title}**\n\nUploaded ✅\nClick Button To Download 👇"

    try:
        # FSUB_CHANNEL hi main channel hai jahan post jayegi
        await client.send_photo(FSUB_CHANNEL, photo, caption=caption, reply_markup=btn)
        await msg.reply("✅ Poster Posted Successfully!")
    except Exception as e:
        await msg.reply(f"❌ Error: {e}")

# ================= 3. SEARCH (USER SIDE) =================
@bot.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.regex(r"^/"))
async def search(_, msg):
    query = clean_name(msg.text)
    if len(query) < 3:
        return

    res = await movies.find_one({"title": {"$regex": query, "$options": "i"}})
    if not res:
        # Message updated as requested
        temp_msg = await msg.reply(f"😞 Bhai abhi hamare pas **'{msg.text}'** movie nahi he.\nThodi der baad try karna.")
        asyncio.create_task(auto_delete(temp_msg, 30))
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

# ================= START & FILE DELIVERY =================
@bot.on_message(filters.private & filters.command("start"))
async def start(_, msg):
    # Fsub Check
    try:
        await bot.get_chat_member(FSUB_CHANNEL, msg.from_user.id)
    except UserNotParticipant:
        start_arg = msg.command[1] if len(msg.command) > 1 else ""
        invite_link = MAIN_CHANNEL_LINK or ""
        try_again_url = f"https://t.me/{bot.me.username}?start={start_arg}" if start_arg else invite_link

        return await msg.reply(
            "❌ **Pehle Channel Join Karo!**\nFir movie milegi.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("JOIN CHANNEL", url=invite_link)],
                [InlineKeyboardButton("TRY AGAIN 🔄", url=try_again_url)]
            ])
        )
    except Exception:
        pass

    if len(msg.command) > 1 and msg.command[1].startswith("file_"):
        mid = msg.command[1].replace("file_", "")
        try:
            res = await movies.find_one({"_id": ObjectId(mid)})
        except:
            res = None
            
        if not res:
            return await msg.reply("❌ File delete ho gayi hai.")

        sent = await bot.send_cached_media(
            msg.chat.id,
            res["file_id"],
            caption=WATERMARK_TEXT
        )
        asyncio.create_task(auto_delete(sent, 120)) # 2 min auto delete file
    else:
        await msg.reply("Bot is Ready! Go to Search Group.")

# ================= ADMIN COMMANDS =================

# 1. Check ID (Agar bot reply na kare to ye use karo)
@bot.on_message(filters.command("id"))
async def my_id(_, msg):
    await msg.reply(f"🆔 Aapki ID: `{msg.from_user.id}`\n\nIs ID ko Render ke ADMIN_IDS me dalo.")

# 2. Stats
@bot.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats(_, msg):
    c = await movies.count_documents({})
    await msg.reply(f"📊 **Total Movies:** {c}")

# 3. Toggle Shortener
@bot.on_message(filters.command("shortn") & filters.user(ADMIN_IDS))
async def short_toggle(_, msg):
    global SHORTLINK_ENABLED
    if len(msg.command) < 2:
        return await msg.reply("Likho: `/shortn on` ya `/shortn off`")
    
    state = msg.command[1].lower()
    if state == "on":
        SHORTLINK_ENABLED = True
        await msg.reply("✅ Shortener ON.")
    elif state == "off":
        SHORTLINK_ENABLED = False
        await msg.reply("❌ Shortener OFF.")

# 4. Delete Movie
@bot.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete(_, msg):
    if len(msg.command) < 2:
        return await msg.reply("❌ Movie ka naam likho. Ex: `/del Pathaan`")
        
    q = " ".join(msg.command[1:])
    r = await movies.delete_many({"title": {"$regex": q, "$options": "i"}})
    await msg.reply(f"🗑 **Deleted:** {r.deleted_count} movies.")

# ================= MAIN =================
async def main():
    await start_web()
    logger.info("Bot Starting...")
    await bot.start()
    logger.info("Bot Started!")
    await idle()
    await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
