"""Task model."""

from datetime import date

from teamflow.extensions import db
from teamflow.timeutils import isoformat_utc, utcnow


class Task(db.Model):
    __tablename__ = "tasks"
    __table_args__ = (
        db.CheckConstraint("status IN ('todo', 'in_progress', 'done')", name="valid_status"),
        db.CheckConstraint("priority IN ('low', 'medium', 'high')", name="valid_priority"),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_to_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="todo")
    priority = db.Column(db.String(20), nullable=False, default="medium")
    due_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    project = db.relationship("Project", back_populates="tasks")
    creator = db.relationship("User", back_populates="created_tasks", foreign_keys=[created_by_id])
    assignee = db.relationship("User", back_populates="assigned_tasks", foreign_keys=[assigned_to_id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "created_by_id": self.created_by_id,
            "assigned_to_id": self.assigned_to_id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "due_date": self.due_date.isoformat() if isinstance(self.due_date, date) else None,
            "created_at": isoformat_utc(self.created_at),
            "updated_at": isoformat_utc(self.updated_at),
        }
