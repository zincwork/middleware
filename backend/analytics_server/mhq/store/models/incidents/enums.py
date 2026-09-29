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
    # A Shortcut bug labelled as a production failure (see production_bugs.py)
    PRODUCTION_BUG = "PRODUCTION_BUG"
    ALERT = "ALERT"


class IncidentBookmarkType(Enum):
    SERVICE = "SERVICE"
