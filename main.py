from pyrogram import enums
import os
import io
import re
import asyncio
import aiohttp
import logging
import base64
from datetime import datetime
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from pyrogram.errors import UserNotParticipant, UserIsBlocked, InputUserDeactivated
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId
from aiohttp import web
from urllib.parse import quote, unquote
from fuzzywuzzy import fuzz
from pyrogram.types import InputMediaPhoto, InputMediaDocument

# ================= CONFIGURATION =================
def get_clean_var(key, default=""):
    val = os.environ.get(key, default)
    return str(val).strip()

API_ID = int(get_clean_var("API_ID", "0"))
API_HASH = get_clean_var("API_HASH", "")
BOT_TOKEN = get_clean_var("BOT_TOKEN", "")
MONGO_URL = get_clean_var("MONGO_URL", "")
TMDB_API_KEY = get_clean_var("TMDB_API_KEY", "")

raw_admins = get_clean_var("ADMIN_IDS", "0").replace(",", " ").split()
ADMIN_IDS = [int(x) for x in raw_admins if x.strip().isdigit()]

def parse_chat_id(key):
    val = get_clean_var(key, "0")
    if val.startswith("-100") or val.lstrip("-").isdigit():
        try:
            return int(val)
        except ValueError:
            return val
    return val

STORAGE_CHANNEL = parse_chat_id("STORAGE_CHANNEL")
SEARCH_CHAT = parse_chat_id("SEARCH_CHAT")
FSUB_CHANNEL = parse_chat_id("FSUB_CHANNEL")
MAIN_CHANNEL_LINK = get_clean_var("MAIN_CHANNEL_LINK", "https://t.me/Movies2026Cinema")
SHORT_DOMAIN = get_clean_var("SHORT_DOMAIN", "arolinks.com")
SHORT_API_KEY = get_clean_var("SHORT_API_KEY", "")

SHORTLINK_ENABLED = True 
PAGE_SIZE = 6 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= BOT CLIENT =================
class MovieBot(Client):
    def __init__(self):
        super().__init__("pratap_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
        self.movies = None
        self.requests = None
        self.users = None

    async def start(self):
        await super().start()
        try:
            mongo_client = AsyncIOMotorClient(MONGO_URL)
            db = mongo_client["PratapCinemaBot"]
            self.movies = db["movies"]
            self.requests = db["movie_requests"]
            self.users = db["users"]
            print("✅ MongoDB Connected Successfully!")
        except Exception as e:
            print(f"❌ MongoDB Connection Error: {e}")

        try:
            await self.set_bot_commands([
                BotCommand("start", "Bot start karein ya file access karein"),
                BotCommand("pratap", "Database ki movies aur bot stats dekhein (Admin)"),
                BotCommand("stats", "Bot ka full status aur count dekhein (Admin)"),
                BotCommand("requests", "Pending movie requests ki list dekhein (Admin)"),
                BotCommand("delreq", "Koi ek movie request delete karein (Admin)"),
                BotCommand("clearreq", "Saari pending requests clear karein (Admin)"),
                BotCommand("shortlink", "Shortlink enable ya disable karein (Admin)"),
                BotCommand("del", "Database se movie delete karein (Admin)"),
                BotCommand("delall", "Database se movies delete karein (Admin)"),
                BotCommand("broadcast", "Sabhi users ko message bhejein (Admin)")
            ])
            print("✅ Telegram Menu Commands Configured!")
        except Exception as e:
            print(f"⚠️ Could not set menu commands: {e}")

        print(f"🚀 BOT STARTED as @{self.me.username}")

    async def stop(self, *args):
        await super().stop()
        print("Bot Stopped.")

app = MovieBot()

# ================= HELPER & UTILS =================
def clean_name(text):
    if not text:
        return ""
    text = str(text).lower()

    text = re.sub(r'@[a-zA-Z0-9_]+', '', text)
    text = re.sub(r'https?://\S+|www\.\S+', '', text)
    text = re.sub(r'\(.*?\)|\[.*?\]', '', text)

    junk = [
        r's\d{1,2}e\d{1,2}', r's\d{1,2}', r'e\d{1,2}', r'season\s*\d+', r'episodes?\s*\d+',
        r'combined', r'complete', r'part\s*\d+', r'vol\s*\d+',
        r'1080p', r'720p', r'480p', r'2160p', r'4k', r'hevc', r'x264', r'x265',
        r'web-?dl', r'web-?rip', r'bluray', r'camrip', r'pre-?dvd', r'hdtv', r'hdrip', r'hsrip',
        r'\bweb\b', r'\bdl\b', r'\bhs\b', r'\bhd\b', r'\bmkv\b', r'\bmp4\b', r'\bavi\b',
        r'hindi', r'english', r'italian', r'dual audio', r'esubs', r'sub',
        r'aac', r'dd5', r'lol', r'ms', r'join'
    ]

    for word in junk:
        text = re.sub(word, '', text, flags=re.IGNORECASE)

    text = re.sub(r'[^a-zA-Z0-9\s]', ' ', text)
    return " ".join(text.split()).strip()

async def notify_admins_about_request(client, user_name, user_id, user_mention, raw_query):
    alert_text = (
        f"📥 **NEW MOVIE REQUEST RECEIVED!**\n\n"
        f"🎬 **Movie Name:** `{raw_query}`\n"
        f"👤 **Requested By:** {user_mention}\n"
        f"🆔 **User ID:** `{user_id}`\n"
        f"🕒 **Time:** `{datetime.now().strftime('%d-%m-%Y %I:%M %p')}`\n\n"
        f"💡 _Upload this movie in Storage Channel to auto-notify the user!_"
    )
    for admin_id in ADMIN_IDS:
        try:
            await client.send_message(chat_id=admin_id, text=alert_text)
        except Exception as e:
            logger.error(f"Could not send request alert to admin {admin_id}: {e}")

async def get_tmdb_corrected_title(query):
    if not TMDB_API_KEY:
        return query
    try:
        clean_q = clean_name(query)
        if not clean_q:
            return query
        url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={quote(clean_q)}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=3.0) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("results", [])
                    if results:
                        top = results[0]
                        corrected = top.get("title") or top.get("name") or top.get("original_title")
                        if corrected:
                            return corrected
    except Exception as e:
        logger.error(f"TMDB Spell Check Error: {e}")
    return query

async def get_poster(query):
    clean_q = clean_name(query)
    if not TMDB_API_KEY or not clean_q: 
        return None
    try:
        url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={quote(clean_q)}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=3.0) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("results"):
                        for item in data["results"]:
                            poster_path = item.get("poster_path")
                            if poster_path:
                                return f"https://image.tmdb.org/t/p/w500{poster_path}"
    except Exception as e:
        logger.error(f"TMDB Poster Error: {e}")
    return None

async def check_upcoming_movie(query):
    if not TMDB_API_KEY:
        return None

    clean_q = re.sub(r'(?i)\b(hindi|dubbed|english|tamil|telugu|full|movie|720p|1080p|480p|web-dl|hdrip|bluray)\b', '', query).strip()
    if not clean_q:
        clean_q = query

    try:
        url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={quote(clean_q)}"
        timeout = aiohttp.ClientTimeout(total=4.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("results", [])
                    if not results:
                        return None

                    today = datetime.now().date()

                    for item in results:
                        rel_date_str = item.get("release_date") or item.get("first_air_date")
                        if rel_date_str:
                            try:
                                rel_date = datetime.strptime(rel_date_str, "%Y-%m-%d").date()
                                days_left = (rel_date - today).days
                                title = item.get("title") or item.get("name") or item.get("original_title") or query
                                poster_path = item.get("poster_path")
                                poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None

                                if days_left > 0:
                                    return {
                                        "title": title,
                                        "release_date": rel_date_str,
                                        "days_remaining": f"{days_left} Days",
                                        "status": "Upcoming",
                                        "poster": poster_url
                                    }
                            except Exception:
                                continue

                    top_item = results[0]
                    rel_date_str = top_item.get("release_date") or top_item.get("first_air_date") or "N/A"
                    title = top_item.get("title") or top_item.get("name") or top_item.get("original_title") or query
                    poster_path = top_item.get("poster_path")
                    poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None

                    days_status = "N/A"
                    if rel_date_str != "N/A":
                        try:
                            rel_date = datetime.strptime(rel_date_str, "%Y-%m-%d").date()
                            if rel_date > today:
                                days_status = f"{(rel_date - today).days} Days"
                            else:
                                days_status = "Already Released"
                        except Exception:
                            days_status = "N/A"

                    return {
                        "title": title,
                        "release_date": rel_date_str,
                        "days_remaining": days_status,
                        "status": "TMDB Found",
                        "poster": poster_url
                    }
    except Exception as e:
        logger.error(f"TMDB Upcoming Error: {e}")
        return None
    return None

# ================= FIXED STRICT SEARCH LOGIC =================
async def smart_db_search(client, query):
    if not query or not query.strip():
        return []

    clean_q = clean_name(query)
    if not clean_q:
        clean_q = query.strip().lower()

    words = clean_q.split()
    all_docs = await client.movies.find({}).to_list(length=3000)
    matched = []
    
    for doc in all_docs:
        doc_title = clean_name(doc.get("title", ""))
        if not doc_title:
            continue
        
        # 1. Direct Substring Match (e.g. 'lenin' in 'lenin 2026')
        if clean_q in doc_title:
            matched.append(doc)
            continue
            
        # 2. Multi-word Match (Agar search me multiple words hon)
        if len(words) > 1 and all(w in doc_title for w in words):
            matched.append(doc)
            continue

        # 3. Strict Fuzzy Match (partial_ratio hata kar token_set_ratio 85+ par set kiya hai)
        if len(clean_q) > 3 and fuzz.token_set_ratio(clean_q, doc_title) >= 85:
            matched.append(doc)

    if not matched:
        corrected_title = await get_tmdb_corrected_title(query)
        clean_corrected = clean_name(corrected_title)
        if clean_corrected and clean_corrected != clean_q:
            for doc in all_docs:
                doc_title = clean_name(doc.get("title", ""))
                if not doc_title:
                    continue
                if clean_corrected in doc_title:
                    matched.append(doc)
                elif len(clean_corrected) > 3 and fuzz.token_set_ratio(clean_corrected, doc_title) >= 85:
                    matched.append(doc)

    return matched

async def get_shortlink(url):
    if not SHORTLINK_ENABLED: return url
    try:
        api_url = f"https://{SHORT_DOMAIN}/api?api={SHORT_API_KEY}&url={url}"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=10) as resp:
                res = await resp.json()
                if res.get("status") == "success": return res["shortenedUrl"]
    except Exception: pass
    return url

async def get_search_buttons(query, results, offset=0):
    btn_list = []
    me = await app.get_me()
    for res in results[offset : offset + PAGE_SIZE]:
        db_id = str(res["_id"])
        db_title = res.get("original_title", res["title"])
        display_name = db_title[:35] + "..." if len(db_title) > 35 else db_title
        bot_url = f"https://t.me/{me.username}?start=file_{db_id}"
        final_link = await get_shortlink(bot_url)
        btn_list.append([InlineKeyboardButton(f"🎬 {display_name}", url=final_link)])
        
    nav_btns = []
    if offset > 0:
        nav_btns.append(InlineKeyboardButton("⬅️ Back", callback_data=f"page_{offset - PAGE_SIZE}_{quote(query)}"))
    if offset + PAGE_SIZE < len(results):
        nav_btns.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{offset + PAGE_SIZE}_{quote(query)}"))
    
    if nav_btns: btn_list.append(nav_btns)
    
    query_b64 = base64.urlsafe_b64encode(query.encode()).decode().rstrip("=")
    btn_list.append([InlineKeyboardButton("📂 GET ALL FILES (IN PM) 📂", url=f"https://t.me/{me.username}?start=all_{query_b64}")])
    return InlineKeyboardMarkup(btn_list)

async def delete_after_delay(msgs, delay):
    await asyncio.sleep(delay)
    for m in msgs:
        try: await m.delete()
        except Exception: pass

# ================= ALL ADMIN COMMAND HANDLERS =================

@app.on_message(filters.command(["pratap", "stats"]) & filters.user(ADMIN_IDS))
async def stats_cmd(client, msg):
    count = await client.movies.count_documents({})
    users_count = await client.users.count_documents({})
    req_count = await client.requests.count_documents({})
    await msg.reply(
        f"📊 **Bot Status (Admin)**\n\n"
        f"🎬 Total Movies: `{count}`\n"
        f"👤 Total Users: `{users_count}`\n"
        f"📌 Pending Requests: `{req_count}`"
    )

@app.on_message(filters.command("requests") & filters.user(ADMIN_IDS))
async def list_requests_cmd(client, msg):
    reqs = await client.requests.find({}).to_list(length=500)
    if not reqs:
        return await msg.reply("✅ Koi pending requests nahi hain!")
        
    header = f"📥 **TOTAL PENDING REQUESTS ({len(reqs)}):**\n\n"
    chunks = []
    current_chunk = header

    for idx, r in enumerate(reqs, 1):
        u_name = r.get("user_name", "User")
        u_id = r.get("user_id", "N/A")
        movie = r.get("raw_query", r.get("query", "Unknown"))
        
        entry = (
            f"**{idx}.** 🎬 `{movie}`\n"
            f"   👤 [{u_name}](tg://user?id={u_id}) (`{u_id}`)\n\n"
        )
        
        if len(current_chunk) + len(entry) > 3800:
            chunks.append(current_chunk)
            current_chunk = entry
        else:
            current_chunk += entry

    if current_chunk:
        chunks.append(current_chunk)

    for chunk in chunks:
        await msg.reply(chunk, disable_web_page_preview=True)

@app.on_message(filters.command("delreq") & filters.user(ADMIN_IDS))
async def delete_single_request_cmd(client, msg):
    if len(msg.command) < 2:
        return await msg.reply("Usage:\n`/delreq movie_name`")
    
    query = clean_name(" ".join(msg.command[1:]))
    res = await client.requests.delete_many({
        "query": {"$regex": query, "$options": "i"}
    })
    await msg.reply(f"🗑️ Cleaned: Deleted `{res.deleted_count}` pending request(s) matching `{query}`.")

@app.on_message(filters.command("clearreq") & filters.user(ADMIN_IDS))
async def clear_all_requests_cmd(client, msg):
    res = await client.requests.delete_many({})
    await msg.reply(f"🗑️ MongoDB Cleaned! Removed all `{res.deleted_count}` pending requests.")

@app.on_message(filters.command("shortlink") & filters.user(ADMIN_IDS))
async def toggle_shortlink_cmd(client, msg):
    global SHORTLINK_ENABLED
    choice = msg.command[1].lower() if len(msg.command) > 1 else ""
    if choice == "on": 
        SHORTLINK_ENABLED = True
        await msg.reply("✅ Shortlink Enabled")
    elif choice == "off": 
        SHORTLINK_ENABLED = False
        await msg.reply("❌ Shortlink Disabled")
    else:
        await msg.reply(f"Status: `{'ON' if SHORTLINK_ENABLED else 'OFF'}`\nUse `/shortlink on` or `/shortlink off`")

@app.on_message(filters.command(["del", "delall"]) & filters.user(ADMIN_IDS))
async def delete_movie_cmd(client, msg):
    if len(msg.command) < 2:
        return await msg.reply("Usage:\n/del movie_name")
    query = clean_name(" ".join(msg.command[1:]))
    result = await client.movies.delete_many({
        "title": {"$regex": query, "$options": "i"}
    })
    await msg.reply(f"🗑️ Deleted: {result.deleted_count} movie(s).")

@app.on_message(filters.command(["broadcast", "sms"]) & filters.user(ADMIN_IDS))
async def broadcast_cmd(client, msg):
    if not msg.reply_to_message:
        return await msg.reply("⚠️ Broadcast bhejne ke liye kisi message ko reply karein `/broadcast` ya `/sms` se.")
        
    status = await msg.reply("📢 **Broadcast shuru ho raha hai...**")
    users = await client.users.find({}).to_list(length=100000)
    
    total = len(users)
    success = 0
    blocked = 0
    failed = 0
    
    for u in users:
        uid = u.get("user_id")
        if not uid: continue
        try:
            await msg.reply_to_message.copy(uid)
            success += 1
            await asyncio.sleep(0.05)
        except (UserIsBlocked, InputUserDeactivated):
            blocked += 1
        except Exception:
            failed += 1
            
    report = (
        f"📊 **Broadcast Finished Report**\n\n"
        f"👥 **Total Users:** `{total}`\n"
        f"✅ **Sent Successfully:** `{success}`\n"
        f"🚫 **Blocked/Deleted:** `{blocked}`\n"
        f"❌ **Failed:** `{failed}`"
    )
    await status.edit(report)

# ================= CALLBACK QUERY HANDLER (PAGINATION) =================
@app.on_callback_query(filters.regex(r"^page_"))
async def page_callback(client, cb):
    try:
        await cb.answer()
        data = cb.data.split("_")
        offset = int(data[1])
        query = unquote("_".join(data[2:]))

        results = await smart_db_search(client, query)
        if not results:
            return await cb.answer("❌ Koi result nahi mila!", show_alert=True)

        reply_markup = await get_search_buttons(query, results, offset=offset)
        await cb.message.edit_reply_markup(reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Page Callback Error: {e}")

# ================= USER HANDLERS =================

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client, msg):
    await client.users.update_one({"user_id": msg.from_user.id}, {"$set": {"user_id": msg.from_user.id}}, upsert=True)
    data = msg.command[1] if len(msg.command) > 1 else ""

    try:
        await client.get_chat_member(FSUB_CHANNEL, msg.from_user.id)
    except UserNotParticipant:
        invite = (await client.get_chat(FSUB_CHANNEL)).invite_link or MAIN_CHANNEL_LINK
        me = await client.get_me()
        buttons = [[InlineKeyboardButton("📢 JOIN CHANNEL", url=invite)]]
        if data:
            try_again_link = f"https://t.me/{me.username}?start={data}"
            buttons.append([InlineKeyboardButton("🔄 TRY AGAIN / VERIFY 🔄", url=try_again_link)])
        btn = InlineKeyboardMarkup(buttons)
        return await msg.reply("❌ Pehle channel join karein!", reply_markup=btn)
    except Exception:
        pass

    if not data:
        group_link = MAIN_CHANNEL_LINK
        if SEARCH_CHAT and SEARCH_CHAT != 0:
            try:
                search_group = await client.get_chat(SEARCH_CHAT)
                group_link = search_group.invite_link or (f"https://t.me/{search_group.username}" if search_group.username else MAIN_CHANNEL_LINK)
            except Exception:
                group_link = MAIN_CHANNEL_LINK

        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔍 GO TO SEARCH GROUP 🔍", url=group_link)]])
        return await msg.reply(
            "👋 **Namaste!**\n\nMovies search karne ke liye niche diye gaye button par click karke hamare **Search Group** me jayein.",
            reply_markup=btn
        )

    if data.startswith("file_"):
        res = await client.movies.find_one({"_id": ObjectId(data.split("_")[1])})
        if res:
            raw_title = res.get('original_title', res.get('title', 'Movie'))
            clean_text = str(raw_title).replace('.', ' ').replace('_', ' ')
            quality_pattern = r'(?i)\(.*?\)|\[.*?\]|\b(s\d+|e\d+|s\d+combined|s\d+complete|season\s*\d+|episode\s*\d+|combined|complete|part\s*\d+|360p|480p|720p|1080p|1080|2160p|4k|2k|hd|hdr|web-?dl|webrip|hdrip|bluray|hdtv|dvdrip|cam|uncut|hindi|tam|tel|eng|dual|multi|esub|sub|aac|ddp5?\.1|mkv|mp4|avi)\b.*'
            clean_title = re.sub(quality_pattern, '', clean_text).strip()
            if not clean_title:
                clean_title = clean_text.strip()

            safe_title = clean_title.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

            # Movie Title Link (Is par click karne se channel khulega aur forward par sath jayega)
            file_caption = (
                f"🎬 <a href='https://t.me/Movies2026Cinema'><b>{safe_title}</b></a>\n"
                f"<b>JOIN ❤️: @Movies2026Cinema</b>"
            )

            poster_url = res.get("poster") or res.get("poster_url")
            if not poster_url and TMDB_API_KEY:
                poster_url = await get_poster(clean_title)

            # Compact Size Poster URL
            if poster_url:
                poster_url = poster_url.replace('/w500/', '/w300/').replace('/original/', '/w300/')

            sent_msgs = []

            # 1. Poster Photo Bhejo
            if poster_url:
                try:
                    p_msg = await client.send_photo(
                        chat_id=msg.chat.id,
                        photo=poster_url
                    )
                    sent_msgs.append(p_msg)
                except Exception:
                    pass

            # 2. Main Video File Bhejo (Title Link ke sath)
            try:
                sf = await client.send_cached_media(
                    chat_id=msg.chat.id, 
                    file_id=res["file_id"], 
                    caption=file_caption, 
                    parse_mode=enums.ParseMode.HTML
                )
                sent_msgs.append(sf)
            except Exception:
                pass

            # 3. Auto-delete Warning Message
            warn_msg = await msg.reply_text(
                "⚠️ **DHYAN DEN:** Is file ko turant apne **Saved Messages** ya kisi doosri jagah **Forward** karke rakh lein, ye 5 minute mein delete ho jayegi!"
            )
            sent_msgs.append(warn_msg)
            asyncio.create_task(delete_after_delay(sent_msgs, 300))
            
    elif data.startswith("all_"):
        try:
            b64_str = data.split("_", 1)[1]
            b64_str += "=" * ((4 - len(b64_str) % 4) % 4)
            search_q = base64.urlsafe_b64decode(b64_str).decode()
        except Exception:
            search_q = unquote(data.split("_", 1)[1])

        results = await smart_db_search(client, search_q)
        if not results:
            return await msg.reply("❌ Files nahi mili!")

        sts = await msg.reply(f"🔍 Found {len(results)} files. Sending...")
        sent_messages = []
        for res in results:
            try:
                raw_title = res.get('original_title', res.get('title', 'Movie'))
                clean_title = str(raw_title).split("\n")[0].split("JOIN")[0].replace("@Movies2026Cinema", "").strip()
                safe_title = clean_title.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                
                cap = (
                    f"<blockquote><a href='https://t.me/Movies2026Cinema'><b>{safe_title}</b></a>\n"
                    f"<b>JOIN ❤️: @Movies2026Cinema</b></blockquote>"
                )
                poster = res.get("poster") or res.get("poster_url")
                if poster:
                    m = await client.send_photo(msg.chat.id, photo=poster, caption=cap, parse_mode=enums.ParseMode.HTML)
                else:
                    m = await client.send_cached_media(msg.chat.id, res["file_id"], caption=cap, parse_mode=enums.ParseMode.HTML)
                sent_messages.append(m)
                await asyncio.sleep(1.2)
            except Exception:
                pass

        warn_sts = await sts.edit(
            "✅ **Batch Complete!**\n\n"
            "⚠️ **DHYAN DEN:** Sabhi files 5 minute mein delete ho jayengi! Inhe turant apne **Saved Messages** mein forward kar lein."
        )
        asyncio.create_task(delete_after_delay(sent_messages + [warn_sts], 300))
            
@app.on_message(filters.private & filters.text & ~filters.regex(r"^/"))
async def pm_text_handler(client, msg):
    group_link = MAIN_CHANNEL_LINK
    if SEARCH_CHAT and SEARCH_CHAT != 0:
        try:
            search_group = await client.get_chat(SEARCH_CHAT)
            group_link = search_group.invite_link or (f"https://t.me/{search_group.username}" if search_group.username else MAIN_CHANNEL_LINK)
        except Exception:
            group_link = MAIN_CHANNEL_LINK

    btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔍 GO TO SEARCH GROUP 🔍", url=group_link)]])
    await msg.reply(
        "👋 **Namaste!**\n\n"
        "Movies search karne ke liye niche diye gaye button par click karke hamare **Search Group** me jayein aur wahan movie ka naam type karein.",
        reply_markup=btn
    )

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.regex(r"^/"))
async def search_movie(client, msg):
    is_admin = msg.from_user and msg.from_user.id in ADMIN_IDS
    query = clean_name(msg.text)
    if len(query) < 2: 
        return

    user_name = msg.from_user.first_name if msg.from_user else "User"
    user_id = msg.from_user.id if msg.from_user else 0
    user_mention = f"[{user_name}](tg://user?id={user_id})" if user_id else "User"

    sw = await client.send_message(msg.chat.id, "🔍 Searching...")

    def format_date(d_str):
        if not d_str or d_str == "N/A":
            return "N/A"
        try:
            return datetime.strptime(d_str.strip(), "%Y-%m-%d").strftime("%d-%m-%Y")
        except Exception:
            return d_str

    results = []
    try:
        results = await asyncio.wait_for(smart_db_search(client, msg.text), timeout=5.0)
    except Exception:
        results = []

    if not results:
        await client.requests.update_one(
            {"user_id": user_id, "query": query},
            {"$set": {
                "user_id": user_id,
                "user_name": user_name,
                "query": query,
                "raw_query": msg.text,
                "time": datetime.now()
            }},
            upsert=True
        )

        asyncio.create_task(notify_admins_about_request(client, user_name, user_id, user_mention, msg.text))

        upcoming_info = None
        try:
            upcoming_info = await asyncio.wait_for(check_upcoming_movie(msg.text), timeout=4.0)
        except Exception:
            upcoming_info = None

        if upcoming_info:
            up_date = format_date(upcoming_info.get('release_date', 'N/A'))
            days_str = upcoming_info.get('days_remaining', 'N/A')
            status_str = upcoming_info.get('status', 'Upcoming')

            text = (
                f"🎬 **Movie:** `{upcoming_info['title']}`\n"
                f"👤 **Requested by:** {user_mention} (`{user_id}`)\n"
                f"📅 **Release Date:** `{up_date}`\n"
                f"📌 **Status:** `{status_str}`\n"
                f"⏳ **Info / Days Left:** `{days_str}`\n\n"
                f"ℹ️ _Ye movie release/available hote hi hamare database me add kar di jayegi!_"
            )
            try:
                await sw.delete()
            except Exception:
                pass

            res_msg = None
            if upcoming_info.get('poster'):
                try:
                    res_msg = await client.send_photo(msg.chat.id, photo=upcoming_info['poster'], caption=text)
                except Exception:
                    res_msg = await client.send_message(msg.chat.id, text=text)
            else:
                res_msg = await client.send_message(msg.chat.id, text=text)

            if not is_admin and res_msg:
                asyncio.create_task(delete_after_delay([res_msg], 300))
            return

        try:
            await sw.delete()
        except Exception:
            pass

        req_msg = await client.send_message(
            msg.chat.id,
            f"👤 **Requested by:** {user_mention} (`{user_id}`)\n\n"
            "Maaf kijiye, ye movie abhi hamare database me available nahi hai.\n\n"
            "Humne aapki request admin ko bhej di hai.\n"
            "Jaise hi movie database me add hogi, aapko automatically private message mil jayega."
        )

        if not is_admin:
            asyncio.create_task(delete_after_delay([req_msg, msg], 60))
        return

    try:
        poster = await get_poster(query)
        markup = await get_search_buttons(query, results, offset=0)
        
        text = (
            f"🎬 **Results for:** 📌 `{msg.text}`\n"
            f"👤 **Requested by:** {user_mention} (`{user_id}`)\n\n"
            f"⏳ _Ye result 5 minute mein delete ho jayega._"
        )
        
        if poster:
            try:
                res_msg = await client.send_photo(msg.chat.id, photo=poster, caption=text, reply_markup=markup)
            except Exception:
                res_msg = await client.send_message(msg.chat.id, text=text, reply_markup=markup)
        else:
            res_msg = await client.send_message(msg.chat.id, text=text, reply_markup=markup)
        
        try:
            await sw.delete()
        except Exception:
            pass
        
        if not is_admin:
            try:
                await msg.delete()
            except Exception:
                pass
            asyncio.create_task(delete_after_delay([res_msg], 300))
            
    except Exception as e:
        logger.error(f"Search Final Error: {e}")

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document | filters.forwarded))
async def add_to_db(client, msg):
    file = msg.video or msg.document
    if not file:
        return

    raw_caption = msg.caption or file.file_name or "Unknown Movie"

    clean_text = raw_caption.replace('.', ' ').replace('_', ' ')
    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', clean_text)
    year = year_match.group(1) if year_match else None

    quality_pattern = r'(?i)\(.*?\)|\[.*?\]|\b(s\d+|e\d+|s\d+combined|s\d+complete|season\s*\d+|episode\s*\d+|combined|complete|part\s*\d+|360p|480p|720p|1080p|1080|2160p|4k|2k|hd|hdr|web-?dl|webrip|hdrip|bluray|hdtv|dvdrip|cam|hindi|tam|tel|eng|dual|multi|esub|sub|aac|ddp5?\.1|mkv|mp4|avi)\b.*'

    if year:
        search_title = clean_text.split(year)[0].strip()
    else:
        search_title = re.sub(quality_pattern, '', clean_text).strip()

    search_title = re.sub(quality_pattern, '', search_title).strip()
    if not search_title:
        search_title = clean_text.strip()

    await client.movies.insert_one({
        "title": search_title,
        "original_title": raw_caption,
        "file_id": file.file_id
    })

    status_msg = await msg.reply_text(f"📁 File DB me Add ho gayi!\nClean Name: `{search_title}`\n⏳ Checking Duplicate...")

    already_posted = await client.movies.count_documents({
        "title": {"$regex": f"^{re.escape(search_title)}$", "$options": "i"}
    })

    if already_posted > 1:
        await status_msg.edit_text(f"📁 File DB me Add ho gayi!\n⚠️ **Duplicate Poster Skipped:** `{search_title}` ka poster pehle se hai.")
        return

    group_link = MAIN_CHANNEL_LINK
    if SEARCH_CHAT and SEARCH_CHAT != 0:
        try:
            search_group = await client.get_chat(SEARCH_CHAT)
            group_link = search_group.invite_link or (f"https://t.me/{search_group.username}" if search_group.username else MAIN_CHANNEL_LINK)
        except Exception:
            group_link = MAIN_CHANNEL_LINK

    poster_url = None
    rel_date = "N/A"
    rating = "N/A"
    title_display = search_title

    if TMDB_API_KEY:
        try:
            encoded_title = search_title.replace(' ', '%20')

            if year:
                url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={encoded_title}&primary_release_year={year}"
            else:
                url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={encoded_title}"

            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("results", [])

                        if not results and year:
                            url_fallback = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={encoded_title}"
                            async with session.get(url_fallback) as resp2:
                                if resp2.status == 200:
                                    data2 = await resp2.json()
                                    results = data2.get("results", [])

                        if not results:
                            url_multi = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={encoded_title}"
                            async with session.get(url_multi) as resp3:
                                if resp3.status == 200:
                                    data3 = await resp3.json()
                                    results = data3.get("results", [])

                        valid_item = None
                        for res in results:
                            if res.get("poster_path"):
                                valid_item = res
                                break

                        if valid_item:
                            title_display = valid_item.get("title") or valid_item.get("name") or search_title
                            rel_date = valid_item.get("release_date") or valid_item.get("first_air_date") or "N/A"
                            rating = valid_item.get("vote_average", "N/A")
                            p_path = valid_item.get("poster_path")
                            poster_url = f"https://image.tmdb.org/t/p/w342{p_path}"

        except Exception as e:
            logger.error(f"TMDB Fetch Error: {e}")
            
    caption_text = (
        f"🎬 **EXCLUSIVE MOVIE DROP** 🎬\n\n"
        f"📌 **TITLE :** `{title_display}`\n"
        f"📅 **RELEASE DATE :** {rel_date}\n"
        f"⭐ **RATING :** {rating} / 10\n"
        f"📁 **FILE NAME :** `{raw_caption}`\n\n"
        f"👇 **DOWNLOAD HERE** 👇\n"
        f"Movie ka naam copy karke search group me likh dena he."
    )

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 GET MOVIE HERE 🔍", url=group_link)]
    ])

    target_channel = FSUB_CHANNEL or "@Movies2026Cinema"
    
    async with aiohttp.ClientSession() as session:
        img_bytes = None
        if poster_url:
            try:
                async with session.get(poster_url) as resp:
                    if resp.status == 200:
                        img_bytes = await resp.read()
            except Exception:
                pass

        try:
            if img_bytes:
                photo_file = io.BytesIO(img_bytes)
                photo_file.name = "poster.jpg"
                await client.send_photo(target_channel, photo=photo_file, caption=caption_text, reply_markup=buttons)
            else:
                await client.send_message(target_channel, text=caption_text, reply_markup=buttons)
            
            await status_msg.edit_text("✅ Database Updated & Channel Poster Posted!")
        except Exception as e:
            logger.error(f"Auto Poster Error: {e}")
            await status_msg.edit_text(f"❌ Channel Post Error: `{e}`")

    try:
        all_requests = await client.requests.find({}).to_list(length=5000)
        for req in all_requests:
            req_q = req.get("query", "")
            if req_q and (req_q in search_title or fuzz.partial_ratio(req_q, search_title) > 80):
                user_id = req.get("user_id")
                try:
                    await client.send_message(
                        user_id,
                        f"🎉 Aapki requested movie **{raw_caption}** ab hamare database me add ho gayi hai!\n\n"
                        f"Search Group me jaakar download kar sakte hain."
                    )
                    await client.requests.delete_one({"_id": req["_id"]})
                except Exception as e:
                    logger.error(f"Failed notification to {user_id}: {e}")
    except Exception as e:
        logger.error(f"Request Notify Loop Error: {e}")

# ================= RUNNER =================
async def start_bot():
    app_web = web.Application()
    app_web.router.add_get("/", lambda r: web.Response(text="Bot Alive"))
    runner = web.AppRunner(app_web)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080))).start()
    await app.start()
    await idle()
    await app.stop()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(start_bot())
