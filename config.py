import os
from dotenv import load_dotenv

# Load variables from the local .env file.
# On Railway, variables are loaded from the Railway Variables section.
load_dotenv()


def get_positive_int(name: str, default: int) -> int:
    """
    Read a positive integer from environment variables.

    Invalid, zero, or negative values fall back to the default.
    """
    raw_value = os.getenv(name, str(default))

    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        print(
            f"Warning: {name}={raw_value!r} is invalid. "
            f"Using default value {default}."
        )
        return default

    if value < 1:
        print(
            f"Warning: {name} must be at least 1. "
            f"Using default value {default}."
        )
        return default

    return value


# ============================================================
# Telegram Bot
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN was not found in environment variables!")


# ============================================================
# DeepSeek API
# ============================================================

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_URL = os.getenv(
    "DEEPSEEK_URL",
    "https://api.deepseek.com/v1/chat/completions",
)

if not DEEPSEEK_API_KEY:
    print(
        "Warning: DEEPSEEK_API_KEY was not found. "
        "AI extraction will not work."
    )


# ============================================================
# Database
# ============================================================

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    # Local development fallback.
    DATABASE_URL = "sqlite+aiosqlite:///./bot_database.db"

    print(
        "Warning: DATABASE_URL was not found. "
        "Using local SQLite database."
    )

elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql+asyncpg://",
        1,
    )

elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://",
        "postgresql+asyncpg://",
        1,
    )


# ============================================================
# Concurrent processing limits
# ============================================================

# Maximum number of PDF jobs processed simultaneously.
MAX_CONCURRENT_JOBS = get_positive_int(
    "MAX_CONCURRENT_JOBS",
    3,
)

# OCR is CPU- and memory-intensive, so keep this lower.
MAX_CONCURRENT_OCR = get_positive_int(
    "MAX_CONCURRENT_OCR",
    1,
)

# Maximum number of simultaneous DeepSeek requests.
MAX_CONCURRENT_AI = get_positive_int(
    "MAX_CONCURRENT_AI",
    4,
)


# ============================================================
# Payments
# ============================================================

# Telegram Stars uses an empty provider token.
PAYMENT_PROVIDER_TOKEN = os.getenv(
    "PAYMENT_PROVIDER_TOKEN",
    "",
)


# ============================================================
# Administrators
# ============================================================

ADMIN_IDS = []

for raw_admin_id in os.getenv("ADMIN_IDS", "").split(","):
    raw_admin_id = raw_admin_id.strip()

    if not raw_admin_id:
        continue

    try:
        ADMIN_IDS.append(int(raw_admin_id))
    except ValueError:
        print(
            f"Warning: Invalid ADMIN_IDS value ignored: "
            f"{raw_admin_id!r}"
        )
