import os
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL") or "sqlite:///./souq_ai_chat.db"
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True)
    phone_number = Column(String, index=True, nullable=False)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


def init_db():
    Base.metadata.create_all(bind=engine)


def save_message(phone_number, role, content):
    with SessionLocal() as session:
        session.add(ChatMessage(phone_number=phone_number, role=role, content=content))
        session.commit()


def get_recent_messages(phone_number, limit):
    with SessionLocal() as session:
        rows = (
            session.query(ChatMessage)
            .filter(ChatMessage.phone_number == phone_number)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(limit)
            .all()
        )
        rows.reverse()
        return [{"role": r.role, "content": r.content} for r in rows]
