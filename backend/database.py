from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_URL = "sqlite:///./calhelper.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# User Table
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)     
    email = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)

    # Establishes relationship so deleting a user can clear their history if needed
    chats = relationship("ChatMessage", back_populates="owner")

# Chat History Table
class ChatMessage(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(String, index=True, nullable=True)
    user_email = Column(String, ForeignKey("users.email"), nullable=True)
    user_query = Column(Text, nullable=False)
    ai_response = Column(Text, nullable=False)

    owner = relationship("User", back_populates="chats", primaryjoin="ChatMessage.user_email == User.email")

# Initialize tables
def init_db():
    Base.metadata.create_all(bind=engine)

# Database Session Dependency injection
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()