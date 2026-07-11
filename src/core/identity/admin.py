from __future__ import annotations

from sqladmin import ModelView

from core.identity.models import PrivilegeAuditEvent, User


class UserAdmin(ModelView, model=User):
    """SQLAdmin view for normal user administration without superuser control."""

    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-users"
    column_list = [User.email, User.full_name, User.is_active, User.is_admin, User.is_superuser]
    column_searchable_list = [User.email, User.full_name]
    column_sortable_list = [User.email, User.full_name, User.is_active, User.is_admin]
    form_excluded_columns = [User.password_hash, User.is_superuser]


class PrivilegeAuditEventAdmin(ModelView, model=PrivilegeAuditEvent):
    """Read-only SQLAdmin view for append-only superuser privilege changes."""

    name = "Privilege audit event"
    name_plural = "Privilege audit events"
    icon = "fa-solid fa-shield-halved"
    can_create = False
    can_edit = False
    can_delete = False
    column_list = [
        PrivilegeAuditEvent.occurred_at,
        PrivilegeAuditEvent.target_user_id,
        PrivilegeAuditEvent.action,
        PrivilegeAuditEvent.reason,
        PrivilegeAuditEvent.actor_description,
    ]
    column_sortable_list = [PrivilegeAuditEvent.occurred_at, PrivilegeAuditEvent.action]
