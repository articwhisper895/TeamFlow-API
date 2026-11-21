"""Project and project-membership models."""

from datetime import datetime, timezone

from teamflow.extensions import db


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    owner = db.relationship("User", foreign_keys=[owner_id])
    memberships = db.relationship(
        "ProjectMembership", back_populates="project", cascade="all, delete-orphan"
    )
    tasks = db.relationship("Task", back_populates="project", cascade="all, delete-orphan")

    def has_member(self, user_id: int) -> bool:
        return any(membership.user_id == user_id for membership in self.memberships)

    def to_dict(self, include_members: bool = False) -> dict:
        result = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "owner_id": self.owner_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        if include_members:
            result["members"] = [membership.to_dict() for membership in self.memberships]
        return result


class ProjectMembership(db.Model):
    __tablename__ = "project_memberships"
    __table_args__ = (
        db.UniqueConstraint("project_id", "user_id", name="uq_project_memberships_project_user"),
        db.CheckConstraint("role IN ('owner', 'member')", name="valid_role"),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="member")
    joined_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    project = db.relationship("Project", back_populates="memberships")
    user = db.relationship("User", back_populates="memberships")

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "name": self.user.name,
            "email": self.user.email,
            "role": self.role,
            "joined_at": self.joined_at.isoformat(),
        }
