import asyncpg
from config import DATABASE_URL

async def init_db():
    conn = await asyncpg.connect(DATABASE_URL)

    # ----- Foydalanuvchilar -----
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            first_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            referred_by TEXT
        )
    ''')

    # ----- Videolar -----
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            code TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            description TEXT
        )
    ''')

    # ----- Referallar -----
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS referrals (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            count INTEGER DEFAULT 0
        )
    ''')

    # ----- Reklama -----
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
    await conn.execute('''
        INSERT INTO ads (id, content_type, file_id, text, caption, send_count)
        VALUES (1, 'empty', NULL, NULL, NULL, 0)
        ON CONFLICT (id) DO NOTHING
    ''')

    # ----- Majburiy obuna -----
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS mandatory_subscriptions (
            id SERIAL PRIMARY KEY,
            type TEXT NOT NULL,
            identifier TEXT NOT NULL,
            limit_count INTEGER NOT NULL,
            current_count INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS user_completed_subs (
            user_id BIGINT NOT NULL,
            sub_id INTEGER NOT NULL,
            completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, sub_id)
        )
    ''')

    await conn.close()


# ==================== FOYDALANUVCHI ====================
async def register_user_start(user_id, referral_code=None):
    """Yangi foydalanuvchini ro'yxatga oladi yoki mavjudni yangilaydi"""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        async with conn.transaction():
            exists = await conn.fetchval("SELECT 1 FROM users WHERE user_id = $1", user_id)
            if not exists:
                # Yangi foydalanuvchi
                await conn.execute(
                    "INSERT INTO users (user_id, referred_by, first_start, last_activity) VALUES ($1, $2, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    user_id, referral_code
                )
                if referral_code:
                    await conn.execute(
                        "UPDATE referrals SET count = count + 1 WHERE code = $1",
                        referral_code
                    )
            else:
                # Mavjud foydalanuvchi - faollikni yangilaymiz
                await conn.execute(
                    "UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE user_id = $1",
                    user_id
                )
    except Exception as e:
        print(f"register_user_start xatosi: {e}")
    finally:
        await conn.close()


async def update_user_activity(user_id: int):
    """Foydalanuvchi faolligini yangilaydi (har bir so'rovda chaqiriladi)"""
    if not user_id:
        return
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        exists = await conn.fetchval("SELECT 1 FROM users WHERE user_id = $1", user_id)
        if exists:
            await conn.execute(
                "UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE user_id = $1",
                user_id
            )
        else:
            await conn.execute(
                "INSERT INTO users (user_id, first_start, last_activity) VALUES ($1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                user_id
            )
    except Exception as e:
        print(f"update_user_activity xatosi: {e}")
    finally:
        await conn.close()


async def get_total_users():
    """Umumiy foydalanuvchilar soni"""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        count = await conn.fetchval("SELECT COUNT(*) FROM users")
        return count or 0
    except Exception as e:
        print(f"get_total_users xatosi: {e}")
        return 0
    finally:
        await conn.close()


async def get_today_new_users():
    """Bugun birinchi marta kelgan yangi foydalanuvchilar soni"""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE DATE(first_start) = CURRENT_DATE"
        )
        return count or 0
    except Exception as e:
        print(f"get_today_new_users xatosi: {e}")
        return 0
    finally:
        await conn.close()


async def get_today_active_users():
    """Bugun botga xabar yozgan yoki botni ishga tushirgan foydalanuvchilar"""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE DATE(last_activity) = CURRENT_DATE"
        )
        return count or 0
    except Exception as e:
        print(f"get_today_active_users xatosi: {e}")
        return 0
    finally:
        await conn.close()


async def get_week_users():
    """So'nggi 7 kun ichida faol bo'lgan foydalanuvchilar"""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE last_activity >= CURRENT_DATE - INTERVAL '7 days'"
        )
        return count or 0
    except Exception as e:
        print(f"get_week_users xatosi: {e}")
        return 0
    finally:
        await conn.close()


async def get_active_users_last_24h():
    """So'nggi 24 soat ichida faol bo'lgan foydalanuvchilar"""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE last_activity >= CURRENT_TIMESTAMP - INTERVAL '24 hours'"
        )
        return count or 0
    except Exception as e:
        print(f"get_active_users_last_24h xatosi: {e}")
        return 0
    finally:
        await conn.close()


async def get_all_user_ids():
    """Barcha foydalanuvchi ID larini qaytaradi"""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch("SELECT user_id FROM users")
        return [r["user_id"] for r in rows]
    except Exception as e:
        print(f"get_all_user_ids xatosi: {e}")
        return []
    finally:
        await conn.close()


# ==================== VIDEOLAR ====================
async def add_video(code: str, file_id: str, description: str = ""):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            "INSERT INTO videos (code, file_id, description) VALUES ($1, $2, $3) ON CONFLICT (code) DO UPDATE SET file_id=$2, description=$3",
            code, file_id, description
        )
    except Exception as e:
        print(f"add_video xatosi: {e}")
    finally:
        await conn.close()


async def get_video(code: str):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        row = await conn.fetchrow("SELECT file_id, description FROM videos WHERE code = $1", code)
        return row
    except Exception as e:
        print(f"get_video xatosi: {e}")
        return None
    finally:
        await conn.close()


async def delete_video(code: str):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("DELETE FROM videos WHERE code = $1", code)
    except Exception as e:
        print(f"delete_video xatosi: {e}")
    finally:
        await conn.close()


async def list_all_videos():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch("SELECT code, description FROM videos ORDER BY code")
        return [(r["code"], r["description"]) for r in rows]
    except Exception as e:
        print(f"list_all_videos xatosi: {e}")
        return []
    finally:
        await conn.close()


# ==================== REFERALLAR ====================
async def create_referral(name, code):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("INSERT INTO referrals (code, name) VALUES ($1, $2)", code, name)
    except Exception as e:
        print(f"create_referral xatosi: {e}")
    finally:
        await conn.close()


async def check_referral_code(code):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        row = await conn.fetchrow("SELECT code FROM referrals WHERE code = $1", code)
        return row is not None
    except Exception as e:
        print(f"check_referral_code xatosi: {e}")
        return False
    finally:
        await conn.close()


async def get_all_referrals():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch("SELECT code, name, count FROM referrals ORDER BY name")
        return [(r["code"], r["name"], r["count"]) for r in rows]
    except Exception as e:
        print(f"get_all_referrals xatosi: {e}")
        return []
    finally:
        await conn.close()


# ==================== REKLAMA ====================
async def set_ad(content_type, file_id=None, text=None, caption=None):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("DELETE FROM ads WHERE id = 1")
        await conn.execute(
            "INSERT INTO ads (id, content_type, file_id, text, caption, send_count) VALUES (1, $1, $2, $3, $4, 0)",
            content_type, file_id, text, caption
        )
    except Exception as e:
        print(f"set_ad xatosi: {e}")
    finally:
        await conn.close()


async def get_ad():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        row = await conn.fetchrow("SELECT content_type, file_id, text, caption, send_count FROM ads WHERE id = 1")
        if row and row["content_type"] != "empty":
            return row
        return None
    except Exception as e:
        print(f"get_ad xatosi: {e}")
        return None
    finally:
        await conn.close()


async def remove_ad():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            "UPDATE ads SET content_type='empty', file_id=NULL, text=NULL, caption=NULL, send_count=0 WHERE id=1"
        )
    except Exception as e:
        print(f"remove_ad xatosi: {e}")
    finally:
        await conn.close()


async def increment_ad_count():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("UPDATE ads SET send_count = send_count + 1 WHERE id = 1")
    except Exception as e:
        print(f"increment_ad_count xatosi: {e}")
    finally:
        await conn.close()


# ==================== MAJBURIY OBUNA ====================
async def get_active_mandatory_subs():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(
            "SELECT id, type, identifier, limit_count, current_count FROM mandatory_subscriptions WHERE is_active = 1"
        )
        return [{"id": r["id"], "type": r["type"], "identifier": r["identifier"],
                 "limit": r["limit_count"], "count": r["current_count"]} for r in rows]
    except Exception as e:
        print(f"get_active_mandatory_subs xatosi: {e}")
        return []
    finally:
        await conn.close()


async def is_user_completed_sub(user_id: int, sub_id: int) -> bool:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        row = await conn.fetchval(
            "SELECT 1 FROM user_completed_subs WHERE user_id = $1 AND sub_id = $2",
            user_id, sub_id
        )
        return row is not None
    except Exception as e:
        print(f"is_user_completed_sub xatosi: {e}")
        return False
    finally:
        await conn.close()


async def mark_user_completed_sub(user_id: int, sub_id: int) -> bool:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        async with conn.transaction():
            existing = await conn.fetchval(
                "SELECT 1 FROM user_completed_subs WHERE user_id = $1 AND sub_id = $2",
                user_id, sub_id
            )
            if existing:
                return False

            await conn.execute(
                "INSERT INTO user_completed_subs (user_id, sub_id) VALUES ($1, $2)",
                user_id, sub_id
            )
            await conn.execute(
                "UPDATE mandatory_subscriptions SET current_count = current_count + 1 WHERE id = $1",
                sub_id
            )

            row = await conn.fetchrow(
                "SELECT current_count, limit_count FROM mandatory_subscriptions WHERE id = $1",
                sub_id
            )
            deactivated = False
            if row and row["current_count"] >= row["limit_count"]:
                await conn.execute(
                    "UPDATE mandatory_subscriptions SET is_active = 0 WHERE id = $1",
                    sub_id
                )
                deactivated = True
            return deactivated
    except Exception as e:
        print(f"mark_user_completed_sub xatosi: {e}")
        return False
    finally:
        await conn.close()


async def set_user_completed_sub(user_id: int, sub_id: int, completed: bool = True):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        if completed:
            await conn.execute(
                "INSERT INTO user_completed_subs (user_id, sub_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                user_id, sub_id
            )
        else:
            await conn.execute(
                "DELETE FROM user_completed_subs WHERE user_id = $1 AND sub_id = $2",
                user_id, sub_id
            )
    except Exception as e:
        print(f"set_user_completed_sub xatosi: {e}")
    finally:
        await conn.close()


async def add_mandatory_subscription(sub_type: str, identifier: str, limit_count: int):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            "INSERT INTO mandatory_subscriptions (type, identifier, limit_count) VALUES ($1, $2, $3)",
            sub_type, identifier, limit_count
        )
    except Exception as e:
        print(f"add_mandatory_subscription xatosi: {e}")
    finally:
        await conn.close()


async def remove_mandatory_subscription(sub_id: int):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("DELETE FROM mandatory_subscriptions WHERE id = $1", sub_id)
    except Exception as e:
        print(f"remove_mandatory_subscription xatosi: {e}")
    finally:
        await conn.close()


async def list_mandatory_subscriptions():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(
            "SELECT id, type, identifier, limit_count, current_count, is_active FROM mandatory_subscriptions ORDER BY id"
        )
        return rows
    except Exception as e:
        print(f"list_mandatory_subscriptions xatosi: {e}")
        return []
    finally:
        await conn.close()
