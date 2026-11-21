"""User model and password handling."""

from werkzeug.security import check_password_hash, generate_password_hash

from teamflow.extensions import db
from teamflow.timeutils import isoformat_utc, utcnow


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    memberships = db.relationship(
        "ProjectMembership", back_populates="user", cascade="all, delete-orphan"
    )
    created_tasks = db.relationship("Task", back_populates="creator", foreign_keys="Task.created_by_id")
    assigned_tasks = db.relationship("Task", back_populates="assignee", foreign_keys="Task.assigned_to_id")
    refresh_sessions = db.relationship(
        "RefreshSession", back_populates="user", cascade="all, delete-orphan"
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "created_at": isoformat_utc(self.created_at),
        }
