from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String)
    last_name = Column(String)
    email = Column(String, unique=True, index=True)
    institutional_email = Column(String, unique=True, index=True, nullable=True)
    orcid_id = Column(String, unique=True, index=True, nullable=True)
    hashed_password = Column(String)
    position_role = Column(String)
    reason_for_joining = Column(String)
    institution = Column(String)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    owner = relationship("User")
