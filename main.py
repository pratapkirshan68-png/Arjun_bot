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

# ================= CONFIG (ध्यान से भरें) =================
# अगर Render पर error आये तो Default values (0 या "") हटा दें
API_ID = int(os.environ.get("API_ID", 0)) 
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MONGO_URL = os.environ.get("MONGO_URL", "")

# चैनल IDs (कोशिश करें कि ये int में हों और -100 से शुरू हों)
# Example: -1001234567890
STORAGE_CHANNEL = int(os.environ.get("STORAGE_CHANNEL", 0))
SEARCH_CHAT = int(os.environ.get("SEARCH_CHAT", 0)) # यह वो ग्रुप है जहाँ सर्च होगा
FSUB_CHANNEL = int(os.environ.get("FSUB_CHANNEL", 0)) # यह पोस्ट करने वाला और जॉइन करने वाला चैनल है

MAIN_CHANNEL_LINK = os.environ.get("MAIN_CHANNEL_LINK", "https://t.me/YourChannel") 
SEARCH_GROUP_LINK = os.environ.get("SEARCH_GROUP_LINK", "https://t.me/YourGroup")

SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN")
SHORT_API_KEY = os.environ.get("SHORT_API_KEY")

# Admin IDs को सुरक्षित तरीके से निकालना
try:
    ADMIN_IDS = [int(x) for x in str(os.environ.get("ADMIN_IDS", "")).replace(',', ' ').split() if x.strip()]
except ValueError:
    logger.error("ADMIN_IDS environment variable sahi nahi hai!")
    ADMIN_IDS = []

SHORTLINK_ENABLED = True

# ===== WATERMARK TEXT =====
WATERMARK_TEXT = (
    "👁️ 2  [Movies 2026 - Cinema Pratap ❤️🌹]\n\n"
    "⚠️ Ye file 2 minute me auto delete ho jayegi!"
)

# ================= DB CONNECTION =================
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["PratapCinemaBot"]
movies = db["movies"]

# ================= WEB SERVER (Render Keep-Alive) =================
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
    bot_token=BOT_TOKEN,
    in_memory=True # Render ke liye zaroori
)

# ================= HELPERS =================
def clean_name(text):
    if not text:
        return "unknown"
    text = text.lower()
    junk = ['1080p','720p','480p','x264','x265','hevc','hindi','english', 'dual audio', 'web-dl']
    for j in junk:
        text = text.replace(j, '')
    return " ".join(text.replace('.', ' ').replace('_',' ').replace('-', ' ').split())

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

# ================= 1. AUTO ADD MOVIE (STORAGE CHANNEL) =================
@bot.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_movie(_, msg):
    file = msg.video or msg.document
    original_caption = msg.caption or file.file_name or "unknown"
    title = clean_name(original_caption)
    
    # Duplicate Check
    exist = await movies.find_one({"file_id": file.file_id})
    if exist:
        sent = await msg.reply(f"❌ **{title}** pehle se added hai!")
        asyncio.create_task(auto_delete(sent, 60))
        return

    await movies.insert_one({
        "title": title,
        "file_id": file.file_id,
        "caption": original_caption
    })
    
    sent = await msg.reply(f"✅ **Movie Added:**\n`{title}`")
    asyncio.create_task(auto_delete(sent, 60))

# ================= 2. ADMIN COMMANDS =================

# --- A. /pratap (Check Stats) ---
@bot.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats(_, msg):
    try:
        c = await movies.count_documents({})
        await msg.reply(f"📊 **Total Movies in DB:** {c}")
    except Exception as e:
        await msg.reply(f"❌ Error: {e}")

# --- B. /del (Delete Movie) ---
@bot.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete_movie(_, msg):
    if len(msg.command) < 2:
        return await msg.reply("❌ Movie ka naam likho.\nExample: `/del Pathaan`")
        
    query = " ".join(msg.command[1:])
    result = await movies.delete_many({"title": {"$regex": query, "$options": "i"}})
    await msg.reply(f"🗑 **Deleted:** {result.deleted_count} movies matching '{query}'.")

# --- C. /shortn (Toggle Shortener) ---
@bot.on_message(filters.command("shortn") & filters.user(ADMIN_IDS))
async def toggle_shortener(_, msg):
    global SHORTLINK_ENABLED
    if len(msg.command) < 2:
        return await msg.reply("Likho: `/shortn on` ya `/shortn off`")
    
    state = msg.command[1].lower()
    if state == "on":
        SHORTLINK_ENABLED = True
        await msg.reply("✅ Shortener ON kar diya gaya hai.")
    elif state == "off":
        SHORTLINK_ENABLED = False
        await msg.reply("❌ Shortener OFF kar diya gaya hai.")

# --- D. /postar (Post to Channel) ---
@bot.on_message(filters.command("postar") & filters.user(ADMIN_IDS))
async def postar_cmd(client, msg):
    # Yeh message Main Channel (FSUB_CHANNEL) me jayega
    target_chat = FSUB_CHANNEL 
    
    reply = msg.reply_to_message
    if not reply:
        return await msg.reply("❌ Kisi Photo ya Text pe reply karke `/postar` likho.")

    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 SEARCH & DOWNLOAD HERE", url=SEARCH_GROUP_LINK)]
    ])

    try:
        # Agar custom caption likha hai command ke aage
        custom_text = " ".join(msg.command[1:])
        
        if reply.photo:
            file_id = reply.photo.file_id
            caption = custom_text if custom_text else (reply.caption or "🎬 New Movie Uploaded")
            await client.send_photo(target_chat, file_id, caption=caption, reply_markup=btn)
        else:
            text = custom_text if custom_text else (reply.text or "Update")
            await client.send_message(target_chat, text, reply_markup=btn)
            
        await msg.reply("✅ Post Successfully Sent!")
    except Exception as e:
        await msg.reply(f"❌ Error: {e}\n(Check Channel ID and Admin Rights)")

# ================= 3. USER SEARCH (GROUP ONLY) =================
@bot.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.regex(r"^/"))
async def group_search(_, msg):
    query = clean_name(msg.text)
    if len(query) < 2:
        return

    # DB Search
    res = await movies.find_one({"title": {"$regex": query, "$options": "i"}})
    
    if not res:
        # Agar movie nahi mili
        temp = await msg.reply(f"😞 Bhai **'{msg.text}'** abhi available nahi hai.\nSpelling check karo ya thodi der baad try karna.")
        asyncio.create_task(auto_delete(temp, 20))
        return

    # Link Generation
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=file_{res['_id']}"
    final_link = await shortlink(link)

    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 GET MOVIE HERE 📂", url=final_link)]
    ])

    await msg.reply(
        f"🎥 **Movie Found:** `{res['title']}`\n\n👇 **Download ke liye button dabayein:**",
        reply_markup=btn
    )

# ================= 4. PRIVATE CHAT HANDLING (Start & Redirect) =================

# A. /start command
@bot.on_message(filters.private & filters.command("start"))
async def start_handler(_, msg):
    # FSub Check
    if FSUB_CHANNEL:
        try:
            await bot.get_chat_member(FSUB_CHANNEL, msg.from_user.id)
        except UserNotParticipant:
            start_arg = msg.command[1] if len(msg.command) > 1 else ""
            invite_link = MAIN_CHANNEL_LINK
            try_again = f"https://t.me/{bot.me.username}?start={start_arg}" if start_arg else invite_link

            return await msg.reply(
                "⚠️ **Access Denied!**\n\nPehle hamara Main Channel join karo movie lene ke liye.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("JOIN CHANNEL ✅", url=invite_link)],
                    [InlineKeyboardButton("TRY AGAIN 🔄", url=try_again)]
                ])
            )
        except Exception:
            pass # User joined hai

    # File delivery logic
    if len(msg.command) > 1 and msg.command[1].startswith("file_"):
        mid = msg.command[1].replace("file_", "")
        try:
            res = await movies.find_one({"_id": ObjectId(mid)})
        except:
            res = None
            
        if not res:
            return await msg.reply("❌ Ye file delete ho gayi hai ya galat link hai.")

        sent = await bot.send_cached_media(
            msg.chat.id,
            res["file_id"],
            caption=WATERMARK_TEXT
        )
        asyncio.create_task(auto_delete(sent, 120)) # 2 min delete
    else:
        # Normal Start Message
        await msg.reply(f"👋 Namaste {msg.from_user.first_name}!\n\nMain Movie Bot hu. Movie search karne ke liye Search Group me jayein.")

# B. Redirect User if they search in BOT PM
@bot.on_message(filters.private & filters.text & ~filters.command(["start", "pratap", "del", "shortn", "postar", "id"]))
async def redirect_user(_, msg):
    # Agar user bot ke PM me movie ka naam likhta hai
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 SEARCH HERE (Hindi Me)", url=SEARCH_GROUP_LINK)]
    ])
    
    await msg.reply(
        "❌ **Bhai yahan search mat karo!**\n\nMovie search karne ke liye hamare **Search Group** me jao aur wahan naam likho.\n\n👇 Niche button dabao:",
        reply_markup=btn
    )

# ================= MAIN =================
async def main():
    await start_web() # Web server start for Render
    logger.info("Bot Starting...")
    try:
        await bot.start()
        logger.info("Bot Started Successfully!")
    except Exception as e:
        logger.error(f"Bot Start Error: {e}")
        return
    await idle()
    await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
