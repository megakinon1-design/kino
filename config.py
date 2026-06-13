import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN topilmadi")
if not ADMIN_ID:
    raise ValueError("ADMIN_ID topilmadi")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL topilmadi")

ADMIN_ID = int(ADMIN_ID)
