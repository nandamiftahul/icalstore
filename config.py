import os
from dotenv import load_dotenv

# load .env dari root project (lokal)
load_dotenv()

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

    _db = os.getenv("DATABASE_URL")
    if _db:
        SQLALCHEMY_DATABASE_URI = _db.replace("postgres://", "postgresql://", 1)
    else:
        # fallback kalau belum set apa-apa
        SQLALCHEMY_DATABASE_URI = "sqlite:///local.db"

    SQLALCHEMY_TRACK_MODIFICATIONS = False
