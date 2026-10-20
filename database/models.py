from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)

from .connection import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    tg_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String, nullable=True)

    # --- 5 RC Weekly Limit Logic 💅 ---
    # We use 'weekly_requests' to track the 5-PDF limit.
    # 'last_request_date' marks the start of the 7-day cycle.
    weekly_requests = Column(Integer, default=0)
    last_request_date = Column(Date, default=date.today)

    is_pro = Column(Boolean, default=False)
    expiry_date = Column(DateTime, nullable=True)

    # Custom Jinja2 Template
    template_text = Column(Text, nullable=True)

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self):
        status = "PRO" if self.is_pro else "FREE"

        return (
            f"<User {self.tg_id} ({status}) - "
            f"{self.weekly_requests}/5 RCs used this week>"
        )


class ChatMessage(Base):
    """
    Temporary Alice conversation memory.

    This table is completely separate from the users/subscription table.
    Rows are automatically deleted after the 24-hour memory window.
    """

    __tablename__ = "chat_messages"

    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    # Telegram user who is talking to Alice.
    tg_user_id = Column(
        BigInteger,
        nullable=False,
        index=True,
    )

    # Telegram chat ID.
    # Private chat: normally the user's Telegram ID.
    # Group/supergroup: the group's negative Telegram chat ID.
    chat_id = Column(
        BigInteger,
        nullable=False,
        index=True,
    )

    # "user" or "assistant"
    role = Column(
        String(16),
        nullable=False,
    )

    # Conversation text only.
    # PDF/Rate Confirmation contents are not stored here.
    content = Column(
        Text,
        nullable=False,
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )

    __table_args__ = (
        Index(
            "ix_chat_messages_conversation_created",
            "tg_user_id",
            "chat_id",
            "created_at",
        ),
    )

    def __repr__(self):
        return (
            f"<ChatMessage user={self.tg_user_id} "
            f"chat={self.chat_id} role={self.role}>"
        )
