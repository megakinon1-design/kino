import asyncpg
from config import DATABASE_URL

async def init_db():
    conn = await asyncpg.connect(DATABASE_URL)
    # Videos jadvali
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            code TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            description TEXT
        )
    ''')
    # Users jadvali
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            first_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # referral ustuni (mavjud bo‘lmasa qo‘shiladi)
    await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by TEXT")
    # Referallar jadvali
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS referrals (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            count INTEGER DEFAULT 0
        )
    ''')
    # Reklama jadvali (start va kino kodidan keyin chiqadigan)
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS ads (
            id INTEGER PRIMARY KEY DEFAULT 1,
            content_type TEXT NOT NULL,
            file_id TEXT,
            text TEXT,
            caption TEXT,
            send_count INTEGER DEFAULT 0
        )
    ''')
    # Agar ads jadvali bo‘sh bo‘lsa, DEFAULT 1 qator qo‘shamiz (ixtiyoriy, keyinchalik set_ad bilan to‘ldiriladi)
    await conn.execute('''
        INSERT INTO ads (id, content_type, file_id, text, caption, send_count)
        VALUES (1, 'empty', NULL, NULL, NULL, 0)
        ON CONFLICT (id) DO NOTHING
    ''')
    await conn.close()

# ==================== Video funksiyalar ====================
async def add_video(code, file_id, description=""):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        "INSERT INTO videos (code, file_id, description) VALUES ($1, $2, $3) ON CONFLICT (code) DO UPDATE SET file_id=$2, description=$3",
        code, file_id, description
    )
    await conn.close()

async def get_video(code):
    conn = await asyncpg.connect(DATABASE_URL)
    row = await conn.fetchrow("SELECT file_id, description FROM videos WHERE code = $1", code)
    await conn.close()
    return row

async def delete_video(code):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("DELETE FROM videos WHERE code = $1", code)
    await conn.close()

async def list_all_videos():
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("SELECT code, description FROM videos")
    await conn.close()
    return [(r["code"], r["description"]) for r in rows]

# ==================== Foydalanuvchi funksiyalari ====================
async def register_user(user_id):
    """Eski nom, ichida yangi funksiyani chaqiradi"""
    await register_user_start(user_id)

async def register_user_start(user_id, referral_code=None):
    conn = await asyncpg.connect(DATABASE_URL)
    async with conn.transaction():
        exists = await conn.fetchval("SELECT 1 FROM users WHERE user_id = $1", user_id)
        if not exists:
            await conn.execute(
                "INSERT INTO users (user_id, referred_by) VALUES ($1, $2)",
                user_id, referral_code
            )
            if referral_code:
                ref_exists = await conn.fetchval("SELECT 1 FROM referrals WHERE code = $1", referral_code)
                if ref_exists:
                    await conn.execute(
                        "UPDATE referrals SET count = count + 1 WHERE code = $1",
                        referral_code
                    )
        else:
            await conn.execute(
                "UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE user_id = $1",
                user_id
            )
    await conn.close()

async def get_total_users():
    conn = await asyncpg.connect(DATABASE_URL)
    count = await conn.fetchval("SELECT COUNT(*) FROM users")
    await conn.close()
    return count

async def get_today_users():
    conn = await asyncpg.connect(DATABASE_URL)
    count = await conn.fetchval("SELECT COUNT(*) FROM users WHERE DATE(first_start) = CURRENT_DATE")
    await conn.close()
    return count

async def get_week_users():
    conn = await asyncpg.connect(DATABASE_URL)
    count = await conn.fetchval("SELECT COUNT(*) FROM users WHERE first_start >= CURRENT_DATE - INTERVAL '7 days'")
    await conn.close()
    return count

async def get_active_users_last_24h():
    conn = await asyncpg.connect(DATABASE_URL)
    count = await conn.fetchval("SELECT COUNT(*) FROM users WHERE last_activity >= CURRENT_TIMESTAMP - INTERVAL '1 day'")
    await conn.close()
    return count

async def get_all_user_ids():
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("SELECT user_id FROM users")
    await conn.close()
    return [row["user_id"] for row in rows]

# ==================== Referal funksiyalari ====================
async def create_referral(name, code):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        "INSERT INTO referrals (code, name) VALUES ($1, $2)",
        code, name
    )
    await conn.close()

async def check_referral_code(code):
    conn = await asyncpg.connect(DATABASE_URL)
    row = await conn.fetchrow("SELECT code FROM referrals WHERE code = $1", code)
    await conn.close()
    return row is not None

async def get_all_referrals():
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("SELECT code, name, count FROM referrals ORDER BY name")
    await conn.close()
    return [(r["code"], r["name"], r["count"]) for r in rows]

# ==================== Reklama funksiyalari ====================
async def set_ad(content_type, file_id=None, text=None, caption=None):
    """Eski reklamani o'chirib, yangisini yozadi (id=1)."""
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("DELETE FROM ads WHERE id = 1")
    await conn.execute(
        "INSERT INTO ads (id, content_type, file_id, text, caption, send_count) VALUES (1, $1, $2, $3, $4, 0)",
        content_type, file_id, text, caption
    )
    await conn.close()

async def get_ad():
    """Joriy reklamani qaytaradi yoki None."""
    conn = await asyncpg.connect(DATABASE_URL)
    row = await conn.fetchrow("SELECT content_type, file_id, text, caption, send_count FROM ads WHERE id = 1")
    await conn.close()
    if row and row["content_type"] != "empty":
        return row
    return None

async def remove_ad():
    """Reklamani o'chiradi (empty holatiga qaytaradi)."""
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("UPDATE ads SET content_type='empty', file_id=NULL, text=NULL, caption=NULL, send_count=0 WHERE id=1")
    await conn.close()

async def increment_ad_count():
    """Reklama yuborilganda hisoblagichni oshiradi."""
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("UPDATE ads SET send_count = send_count + 1 WHERE id = 1")
    await conn.close()
