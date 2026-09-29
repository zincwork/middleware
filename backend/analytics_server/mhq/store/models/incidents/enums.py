from enum import Enum


class IncidentProvider(Enum):
    GITHUB = "github"
    GITLAB = "gitlab"


class IncidentSource(Enum):
    INCIDENT_SERVICE = "INCIDENT_SERVICE"
    INCIDENT_TEAM = "INCIDENT_TEAM"
    GIT_REPO = "GIT_REPO"


class ServiceStatus(Enum):
    DISABLED = "disabled"
    ACTIVE = "active"
    WARNING = "warning"
    CRITICAL = "critical"
    MAINTENANCE = "maintenance"


class IncidentStatus(Enum):
    TRIGGERED = "triggered"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class IncidentType(Enum):
    INCIDENT = "INCIDENT"
    REVERT_PR = "REVERT_PR"
    # A Shortcut bug labelled as a regression: a change broke it (see regressions.py)
    REGRESSION = "REGRESSION"
    # A deploy that was undone by redeploying an older revision (see rollbacks.py)
    ROLLBACK = "ROLLBACK"
    ALERT = "ALERT"


class IncidentBookmarkType(Enum):
    SERVICE = "SERVICE"
