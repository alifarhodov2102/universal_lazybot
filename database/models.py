from sqlalchemy import Column, Integer, BigInteger, String, Boolean, DateTime, Text, Date
from datetime import datetime, date
from .connection import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    tg_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String, nullable=True)
    
    # --- 5 RC Weekly Limit Logic 💅 ---
    # We use 'weekly_requests' to track the 5-PDF limit.
    # 'last_request_date' will now mark the start of their 7-day cycle.
    weekly_requests = Column(Integer, default=0)
    last_request_date = Column(Date, default=date.today)
    
    is_pro = Column(Boolean, default=False)
    expiry_date = Column(DateTime, nullable=True)
    
    # Custom Jinja2 Template
    template_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        status = "PRO" if self.is_pro else "FREE"
        return f"<User {self.tg_id} ({status}) - {self.weekly_requests}/5 RCs used this week>"