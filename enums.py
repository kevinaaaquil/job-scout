from enum import StrEnum


class UserPrivilege(StrEnum):
    ADMIN = "admin"
    GUEST = "guest"


class RunStatus(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"


class Freshness(StrEnum):
    ANY = ""
    PAST_DAY = "pd"
    PAST_WEEK = "pw"
    PAST_MONTH = "pm"
    PAST_YEAR = "py"
