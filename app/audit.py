from __future__ import annotations

from datetime import datetime, timezone

from models.document import AuditEvent, ProjectData


def record(project: ProjectData, action: str, detail: str = "") -> AuditEvent:
    ev = AuditEvent(timestamp=datetime.now(timezone.utc).isoformat(), action=action, detail=detail)
    project.audit.append(ev)
    return ev
