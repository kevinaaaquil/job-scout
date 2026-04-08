from enum import StrEnum


class UserPrivilege(StrEnum):
    ADMIN = "admin"
    GUEST = "guest"


class RunStatus(StrEnum):
    """Whether the scheduled script is active or stopped."""
    RUNNING = "running"
    STOPPED = "stopped"


class RunResult(StrEnum):
    """Status of an individual job scout run."""
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


class SearchProvider(StrEnum):
    BRAVE = "brave"
    VERTEX = "vertex"


class Freshness(StrEnum):
    ANY = ""
    PAST_DAY = "pd"
    PAST_WEEK = "pw"
    PAST_MONTH = "pm"
    PAST_YEAR = "py"
