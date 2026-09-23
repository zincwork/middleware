from enum import Enum


class TicketProviders(Enum):
    SHORTCUT = "shortcut"

    @classmethod
    def get_enum(cls, provider: str):
        for member in cls.__members__.values():
            if provider == member.value:
                return member
        return None


class TicketType(Enum):
    FEATURE = "feature"
    BUG = "bug"
    CHORE = "chore"
    UNKNOWN = "unknown"

    @classmethod
    def from_provider(cls, value: str):
        if not value:
            return cls.UNKNOWN
        for member in cls.__members__.values():
            if value.lower() == member.value:
                return member
        return cls.UNKNOWN


class TicketStateType(Enum):
    """The provider's own bucketing of a workflow state.

    Shortcut tags every workflow state as one of these four. Keeping the
    provider's bucket rather than inventing our own means a workspace can
    rename or add states without breaking the metrics.
    """

    BACKLOG = "backlog"
    UNSTARTED = "unstarted"
    STARTED = "started"
    DONE = "done"
    UNKNOWN = "unknown"

    @classmethod
    def from_provider(cls, value: str):
        if not value:
            return cls.UNKNOWN
        for member in cls.__members__.values():
            if value.lower() == member.value:
                return member
        return cls.UNKNOWN
