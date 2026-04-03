import copy
import logging
import os
import secrets
import threading
from functools import wraps
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, jsonify, request, send_from_directory, session

import job_scout
from db import get_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
logger = logging.getLogger("server")

# Plain-text passwords (MongoDB + .env) for now; hashing later.
ADMIN_USERS_COLLECTION = "admin_users"

APP_TZ = ZoneInfo(os.environ.get("TZ", "Asia/Kolkata"))

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
scheduler = BackgroundScheduler(daemon=True, timezone=APP_TZ)

JOB_ID = "job_scout_daily"
_current_run_lock = threading.Lock()
_current_run_status = {"running": False, "stage": "", "detail": ""}

# ── Auth ──────────────────────────────────────────────────────────────

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


def _mongo_has_login_users():
    try:
        col = get_db()[ADMIN_USERS_COLLECTION]
        return (
            col.count_documents(
                {
                    "username": {"$exists": True, "$nin": [None, ""]},
                    "password": {"$exists": True, "$nin": [None, ""]},
                },
                limit=1,
            )
            > 0
        )
    except Exception as exc:
        logger.warning("MongoDB admin user check failed: %s", exc)
        return False


def _auth_enabled():
    return bool(ADMIN_PASSWORD) or _mongo_has_login_users()


def _credentials_valid(username, password):
    """MongoDB first (plain password on user doc). If no matching Mongo user, fall back to .env."""
    try:
        doc = get_db()[ADMIN_USERS_COLLECTION].find_one({"username": username})
        if doc is not None:
            stored = doc.get("password") or ""
            return stored == password
    except Exception as exc:
        logger.warning("MongoDB login lookup failed: %s", exc)

    if ADMIN_PASSWORD and username == ADMIN_USER:
        return ADMIN_PASSWORD == password
    return False


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _auth_enabled():
            return f(*args, **kwargs)
        if not session.get("authed"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return send_from_directory("static", "login.html")
        return f(*args, **kwargs)
    return decorated


# ── Scheduler helpers ──────────────────────────────────────────────────

def _execute_run():
    with _current_run_lock:
        if _current_run_status["running"]:
            logger.warning("Run already in progress, skipping")
            return None
        _current_run_status["running"] = True
        _current_run_status["stage"] = "starting"
        _current_run_status["detail"] = ""

    def on_progress(stage, detail):
        with _current_run_lock:
            _current_run_status["stage"] = stage
            _current_run_status["detail"] = detail

    try:
        result = job_scout.run(on_progress=on_progress)
        return result
    finally:
        with _current_run_lock:
            _current_run_status["running"] = False
            _current_run_status["stage"] = "idle"
            _current_run_status["detail"] = ""


def _reschedule(cron_expr):
    if scheduler.get_job(JOB_ID):
        scheduler.remove_job(JOB_ID)
    scheduler.add_job(
        _execute_run,
        CronTrigger.from_crontab(cron_expr),
        id=JOB_ID,
        name="Job Scout Daily Run",
        replace_existing=True,
    )
    logger.info("Scheduled job with cron: %s", cron_expr)


def _get_next_run():
    job = scheduler.get_job(JOB_ID)
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None


# ── Routes: Auth ───────────────────────────────────────────────────────

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    user = data.get("username", "")
    pw = data.get("password", "")
    if not _auth_enabled():
        session["authed"] = True
        return jsonify({"message": "OK"})
    if _credentials_valid(user, pw):
        session["authed"] = True
        return jsonify({"message": "OK"})
    return jsonify({"error": "Invalid credentials"}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"message": "Logged out"})


# ── Routes: Static ─────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return send_from_directory("static", "index.html")


# ── Routes: Status ─────────────────────────────────────────────────────

@app.route("/api/status")
@login_required
def api_status():
    history = job_scout.load_run_history()
    last_run = history[0] if history else None

    with _current_run_lock:
        running = _current_run_status["running"]
        stage = _current_run_status["stage"]
        detail = _current_run_status["detail"]

    return jsonify({
        "next_run": _get_next_run(),
        "last_run": {
            "timestamp": last_run["timestamp"],
            "status": last_run["status"],
            "jobs_matched": last_run["jobs_matched"],
        } if last_run else None,
        "running": running,
        "current_stage": stage,
        "current_detail": detail,
    })


# ── Routes: Runs ───────────────────────────────────────────────────────

@app.route("/api/runs")
@login_required
def api_runs():
    history = job_scout.load_run_history()
    summary = []
    for r in history:
        summary.append({
            "id": r["id"],
            "timestamp": r["timestamp"],
            "status": r["status"],
            "jobs_found": r["jobs_found"],
            "jobs_new": r["jobs_new"],
            "jobs_matched": r["jobs_matched"],
            "jobs_borderline": r.get("jobs_borderline", 0),
            "email_sent": r["email_sent"],
            "error": r.get("error"),
        })
    return jsonify(summary)


@app.route("/api/runs/<run_id>")
@login_required
def api_run_detail(run_id):
    doc = job_scout.get_run_by_id(run_id)
    if doc:
        return jsonify(doc)
    return jsonify({"error": "Run not found"}), 404


@app.route("/api/run", methods=["POST"])
@login_required
def api_trigger_run():
    with _current_run_lock:
        if _current_run_status["running"]:
            return jsonify({"error": "A run is already in progress"}), 409

    thread = threading.Thread(target=_execute_run, daemon=True)
    thread.start()
    return jsonify({"message": "Run triggered", "status": "started"})


# ── Routes: Debug ──────────────────────────────────────────────────────

@app.route("/api/debug/search", methods=["POST"])
@login_required
def api_debug_search():
    """Test a single Google query and return raw results."""
    data = request.get_json()
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400
    max_results = data.get("max_results", 5)
    result = job_scout.test_search(query, max_results)
    return jsonify(result)


@app.route("/api/debug/queries")
@login_required
def api_debug_queries():
    """Show the fully rendered queries that would be used in a run."""
    config = job_scout.load_config()
    queries = job_scout.build_queries(config)
    return jsonify(queries)


# ── Routes: Timezone ───────────────────────────────────────────────────

@app.route("/api/timezone")
@login_required
def api_timezone():
    return jsonify({"timezone": str(APP_TZ)})


# ── Routes: Config ─────────────────────────────────────────────────────

def _validate_config_updates(updates):
    """Validate incoming config fields. Returns list of error strings (empty = valid)."""
    errors = []

    if "search" in updates:
        s = updates["search"]
        if not isinstance(s, dict):
            errors.append("search must be an object")
        else:
            if "freshness" in s and s["freshness"] not in ("", "pd", "pw", "pm", "py"):
                errors.append("search.freshness must be one of: pd, pw, pm, py, or empty")
            if "max_results_per_query" in s:
                try:
                    v = int(s["max_results_per_query"])
                    if not (1 <= v <= 20):
                        errors.append("max_results_per_query must be 1-20")
                    else:
                        s["max_results_per_query"] = v
                except (TypeError, ValueError):
                    errors.append("max_results_per_query must be an integer")
            if "sites" in s:
                if not isinstance(s["sites"], list) or not all(isinstance(x, str) for x in s["sites"]):
                    errors.append("search.sites must be a list of strings")

    if "filtering" in updates:
        f = updates["filtering"]
        if not isinstance(f, dict):
            errors.append("filtering must be an object")
        else:
            if "min_score" in f:
                try:
                    v = int(f["min_score"])
                    if not (0 <= v <= 100):
                        errors.append("min_score must be 0-100")
                    else:
                        f["min_score"] = v
                except (TypeError, ValueError):
                    errors.append("min_score must be an integer")
            if "borderline_threshold" in f:
                try:
                    v = int(f["borderline_threshold"])
                    if not (0 <= v <= 100):
                        errors.append("borderline_threshold must be 0-100")
                    else:
                        f["borderline_threshold"] = v
                except (TypeError, ValueError):
                    errors.append("borderline_threshold must be an integer")

    if "schedule" in updates:
        cron = updates["schedule"]
        if not isinstance(cron, str) or not cron.strip():
            errors.append("schedule must be a non-empty string")
        else:
            try:
                CronTrigger.from_crontab(cron.strip())
            except Exception:
                errors.append(f"Invalid cron expression: {cron}")

    if "candidate_profile" in updates:
        if not isinstance(updates["candidate_profile"], str):
            errors.append("candidate_profile must be a string")

    if "recipient_email" in updates:
        email = updates["recipient_email"]
        if not isinstance(email, str):
            errors.append("recipient_email must be a string")
        elif email and "@" not in email:
            errors.append("recipient_email must be a valid email address")

    return errors


def _redact(value):
    if not value or len(value) < 6:
        return "***"
    return value[:3] + "•" * (len(value) - 6) + value[-3:]


def _enrich_config_for_api(config):
    """Add env-sourced secrets (redacted) into config for the API response."""
    c = copy.deepcopy(config)
    c["openai"]["api_key"] = _redact(os.environ.get("OPENAI_API_KEY", ""))
    icloud_email = os.environ.get("ICLOUD_EMAIL", "")
    sender_mail = os.environ.get("SENDER_MAIL", "").strip() or icloud_email
    c["env_secrets"] = {
        "brave_api_key": _redact(os.environ.get("BRAVE_API_KEY", "")),
        "icloud_email": _redact(icloud_email),
        "sender_mail": _redact(sender_mail),
        "app_specific_password": "••••-••••-••••-••••",
    }
    return c


@app.route("/api/config")
@login_required
def api_get_config():
    config = job_scout.load_config()
    return jsonify(_enrich_config_for_api(config))


@app.route("/api/config", methods=["PUT"])
@login_required
def api_update_config():
    current = job_scout.load_config()
    updates = request.get_json()

    errors = _validate_config_updates(updates)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    if "search" in updates:
        current["search"] = updates["search"]
    if "candidate_profile" in updates:
        current["candidate_profile"] = updates["candidate_profile"]
    if "filtering" in updates:
        current["filtering"] = updates["filtering"]
    if "openai" in updates:
        if "model" in updates["openai"]:
            current["openai"]["model"] = updates["openai"]["model"]
    if "recipient_email" in updates:
        current["recipient_email"] = updates["recipient_email"]
    if "schedule" in updates:
        current["schedule"] = updates["schedule"]
        _reschedule(updates["schedule"])

    job_scout.save_config(current)
    return jsonify({"message": "Config updated", "config": _enrich_config_for_api(current)})


@app.route("/api/config/schedule")
@login_required
def api_get_schedule():
    config = job_scout.load_config()
    return jsonify({
        "schedule": config.get("schedule", "0 9 * * *"),
        "next_run": _get_next_run(),
    })


@app.route("/api/config/schedule", methods=["PUT"])
@login_required
def api_update_schedule():
    data = request.get_json()
    cron_expr = data.get("schedule", "").strip()
    if not cron_expr:
        return jsonify({"error": "schedule is required"}), 400

    try:
        CronTrigger.from_crontab(cron_expr)
    except Exception as e:
        return jsonify({"error": f"Invalid cron expression: {e}"}), 400

    config = job_scout.load_config()
    config["schedule"] = cron_expr
    job_scout.save_config(config)
    _reschedule(cron_expr)

    return jsonify({
        "message": "Schedule updated",
        "schedule": cron_expr,
        "next_run": _get_next_run(),
    })


# ── Routes: Seen jobs ─────────────────────────────────────────────────

@app.route("/api/seen", methods=["DELETE"])
@login_required
def api_clear_seen():
    count = job_scout.clear_all_seen()
    return jsonify({"message": f"Cleared {count} seen jobs"})


@app.route("/api/seen/urls", methods=["DELETE"])
@login_required
def api_clear_seen_urls():
    data = request.get_json() or {}
    urls = data.get("urls", [])
    if not urls or not isinstance(urls, list):
        return jsonify({"error": "urls must be a non-empty list"}), 400
    count = job_scout.clear_seen_urls(urls)
    return jsonify({"message": f"Cleared {count} URLs"})


# ── Startup ────────────────────────────────────────────────────────────

def main():
    config = job_scout.load_config()
    job_scout._ensure_indexes()
    cron_expr = config.get("schedule", "0 9 * * *")

    scheduler.start()
    _reschedule(cron_expr)
    logger.info("Server starting. Next run: %s", _get_next_run())

    port = int(os.environ.get("PORT", 6969))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
