import base64
import copy
import logging
import os
import secrets
import tempfile
import threading
from functools import wraps
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

# Decode base64 service account JSON if provided (for Dokku/container deploys)
_gcp_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64", "")
if _gcp_b64 and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
    _tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    _tmp.write(base64.b64decode(_gcp_b64))
    _tmp.close()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _tmp.name

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, jsonify, request, send_from_directory, session
import bcrypt

from pathlib import Path

import yaml

import job_scout
from db import get_db
from enums import UserPrivilege, RunStatus, Freshness, SearchProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
logger = logging.getLogger("server")

# Passwords are stored hashed (bcrypt).
USERS_COLLECTION = "users"

APP_TZ = ZoneInfo(os.environ.get("TZ", "Asia/Kolkata"))

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
scheduler = BackgroundScheduler(daemon=True, timezone=APP_TZ)

_run_locks = {}  # email -> threading.Lock
_run_statuses = {}  # email -> {"running": bool, "stage": str, "detail": str}

def _get_run_state(email):
    if email not in _run_locks:
        _run_locks[email] = threading.Lock()
        _run_statuses[email] = {"running": False, "stage": "", "detail": ""}
    return _run_locks[email], _run_statuses[email]

def _job_id(email):
    return f"job_scout_{email}"

# ── Auth ──────────────────────────────────────────────────────────────



def _ensure_users_collection():
    """Create unique index on email in users collection."""
    col = get_db()[USERS_COLLECTION]
    col.create_index("email", unique=True, sparse=True)


def ensure_user(email, password, privilege=UserPrivilege.GUEST):
    """Create a user with a bcrypt-hashed password if they don't already exist."""
    col = get_db()[USERS_COLLECTION]
    if not col.find_one({"email": email}):
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        col.insert_one({"email": email, "password": hashed, "privilege": privilege})
        logger.info("Created user: %s (privilege: %s)", email, privilege)


def _mongo_has_login_users():
    try:
        col = get_db()[USERS_COLLECTION]
        return (
            col.count_documents(
                {
                    "email": {"$exists": True, "$nin": [None, ""]},
                    "password": {"$exists": True, "$nin": [None, ""]},
                },
                limit=1,
            )
            > 0
        )
    except Exception as exc:
        logger.warning("MongoDB user check failed: %s", exc)
        return False


def _auth_enabled():
    return _mongo_has_login_users()


def _credentials_valid(email, password):
    """Check hashed password from MongoDB users collection."""
    try:
        doc = get_db()[USERS_COLLECTION].find_one({"email": email})
        if doc is not None:
            stored = doc.get("password") or ""
            return bcrypt.checkpw(password.encode(), stored.encode())
    except Exception as exc:
        logger.warning("MongoDB login lookup failed: %s", exc)
    return False


def _get_user_privilege(email):
    """Return the privilege level for a user. Defaults to 'guest'."""
    try:
        doc = get_db()[USERS_COLLECTION].find_one({"email": email})
        if doc:
            return doc.get("privilege", UserPrivilege.GUEST)
    except Exception:
        pass
    return UserPrivilege.GUEST


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


def config_required(f):
    """Load user config and pass as first arg. Returns 404 if no config exists."""
    @wraps(f)
    def decorated(*args, **kwargs):
        email = session.get("email")
        if not email:
            return jsonify({"error": "No user session"}), 401
        config = job_scout.load_config(email)
        if not config:
            return jsonify({"error": "No config found"}), 404
        return f(config, *args, **kwargs)
    return decorated


# ── Scheduler helpers ──────────────────────────────────────────────────

def _execute_run(email):
    lock, status = _get_run_state(email)
    with lock:
        if status["running"]:
            logger.warning("Run already in progress for %s, skipping", email)
            return None
        status["running"] = True
        status["stage"] = "starting"
        status["detail"] = ""

    def on_progress(stage, detail):
        with lock:
            status["stage"] = stage
            status["detail"] = detail

    try:
        result = job_scout.run(email, on_progress=on_progress)
        return result
    finally:
        with lock:
            status["running"] = False
            status["stage"] = "idle"
            status["detail"] = ""


def _schedule_user(email, cron_expr):
    job_id = _job_id(email)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    scheduler.add_job(
        _execute_run,
        CronTrigger.from_crontab(cron_expr),
        args=[email],
        id=job_id,
        name=f"Job Scout: {email}",
        replace_existing=True,
    )
    logger.info("Scheduled job for %s with cron: %s", email, cron_expr)


def _unschedule_user(email):
    job_id = _job_id(email)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        logger.info("Unscheduled job for %s", email)


def _get_next_run(email):
    job = scheduler.get_job(_job_id(email))
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None


# ── Routes: Auth ───────────────────────────────────────────────────────

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    email = data.get("email", "")
    pw = data.get("password", "")
    if not _auth_enabled():
        session["authed"] = True
        return jsonify({"message": "OK"})
    if _credentials_valid(email, pw):
        session["authed"] = True
        session["email"] = email
        session["privilege"] = _get_user_privilege(email)
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
@config_required
def api_status(config):
    email = config["email"]
    history = job_scout.load_run_history()
    last_run = history[0] if history else None

    lock, status = _get_run_state(email)
    with lock:
        running = status["running"]
        stage = status["stage"]
        detail = status["detail"]

    return jsonify({
        "run_status": config.get("run_status", RunStatus.STOPPED),
        "schedule": config.get("schedule", ""),
        "next_run": _get_next_run(email),
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
    email = session.get("email")
    if not email:
        return jsonify({"error": "No user session"}), 401
    lock, status = _get_run_state(email)
    with lock:
        if status["running"]:
            return jsonify({"error": "A run is already in progress"}), 409

    thread = threading.Thread(target=_execute_run, args=[email], daemon=True)
    thread.start()
    return jsonify({"message": "Run triggered", "status": "started"})


@app.route("/api/script/start", methods=["POST"])
@login_required
@config_required
def api_script_start(config):
    email = config["email"]
    if config.get("run_status") == RunStatus.RUNNING:
        return jsonify({"error": "Script is already running"}), 409

    cron_expr = config.get("schedule", "0 9 * * *")
    config["run_status"] = RunStatus.RUNNING
    job_scout.save_config(config)
    _schedule_user(email, cron_expr)
    return jsonify({"message": "Script started", "next_run": _get_next_run(email)})


@app.route("/api/script/stop", methods=["POST"])
@login_required
@config_required
def api_script_stop(config):
    email = config["email"]
    if config.get("run_status") == RunStatus.STOPPED:
        return jsonify({"error": "Script is already stopped"}), 409

    config["run_status"] = RunStatus.STOPPED
    job_scout.save_config(config)
    _unschedule_user(email)
    return jsonify({"message": "Script stopped"})


# ── Routes: Debug ──────────────────────────────────────────────────────

@app.route("/api/debug/search", methods=["POST"])
@login_required
def api_debug_search():
    """Test a single search query and return raw results."""
    data = request.get_json()
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400
    max_results = data.get("max_results", 5)
    provider = data.get("provider", SearchProvider.BRAVE)
    if provider == SearchProvider.VERTEX:
        result = job_scout.test_vertex_search(query, max_results)
    else:
        result = job_scout.test_search(query, max_results)
    return jsonify(result)


@app.route("/api/debug/queries")
@login_required
@config_required
def api_debug_queries(config):
    """Show the fully rendered queries that would be used in a run."""
    queries = job_scout.build_queries(config)
    return jsonify(queries)


# ── Routes: Vertex Target Sites ───────────────────────────────────────

@app.route("/api/vertex/sites")
@login_required
def api_vertex_list_sites():
    """List whitelisted URI patterns from the Vertex datastore."""
    try:
        sites = job_scout.list_vertex_target_sites()
        return jsonify(sites)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/vertex/sites/sync", methods=["POST"])
@login_required
@config_required
def api_vertex_sync_sites(config):
    """Sync the Vertex datastore whitelist to match the patterns stored in config."""
    patterns = config.get("search", {}).get("vertex_sites", [])
    if not patterns:
        return jsonify({"error": "No vertex_sites in config to sync"}), 400
    try:
        result = job_scout.sync_vertex_target_sites(patterns)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
            if "freshness" in s and s["freshness"] not in Freshness:
                errors.append(f"search.freshness must be one of: {', '.join(f.value for f in Freshness)}")
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
    email = session.get("email")
    if not email:
        return jsonify({"error": "No user session"}), 401
    config = job_scout.load_config(email)
    if not config:
        config = job_scout.seed_config(email)
    return jsonify(_enrich_config_for_api(config))


@app.route("/api/config", methods=["PUT"])
@login_required
@config_required
def api_update_config(config):
    if config.get("run_status") == RunStatus.RUNNING:
        return jsonify({"error": "Stop the script before editing config"}), 409

    updates = request.get_json()

    errors = _validate_config_updates(updates)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    if "search" in updates:
        config["search"] = updates["search"]
    if "candidate_profile" in updates:
        config["candidate_profile"] = updates["candidate_profile"]
    if "filtering" in updates:
        config["filtering"] = updates["filtering"]
    if "openai" in updates:
        if "model" in updates["openai"]:
            config["openai"]["model"] = updates["openai"]["model"]
    if "recipient_email" in updates:
        config["recipient_email"] = updates["recipient_email"]
    if "schedule" in updates:
        config["schedule"] = updates["schedule"]

    job_scout.save_config(config)
    return jsonify({"message": "Config updated", "config": config})


@app.route("/api/config/schedule")
@login_required
@config_required
def api_get_schedule(config):
    return jsonify({
        "schedule": config.get("schedule", "0 9 * * *"),
        "next_run": _get_next_run(config["email"]),
    })


@app.route("/api/config/schedule", methods=["PUT"])
@login_required
@config_required
def api_update_schedule(config):
    if config.get("run_status") == RunStatus.RUNNING:
        return jsonify({"error": "Stop the script before editing config"}), 409

    data = request.get_json()
    cron_expr = data.get("schedule", "").strip()
    if not cron_expr:
        return jsonify({"error": "schedule is required"}), 400

    try:
        CronTrigger.from_crontab(cron_expr)
    except Exception as e:
        return jsonify({"error": f"Invalid cron expression: {e}"}), 400

    config["schedule"] = cron_expr
    job_scout.save_config(config)

    return jsonify({
        "message": "Schedule updated",
        "schedule": cron_expr,
        "next_run": _get_next_run(config["email"]),
    })


# ── Routes: Seen jobs ─────────────────────────────────────────────────

@app.route("/api/seen")
@login_required
def api_list_seen():
    jobs = job_scout.list_seen_jobs()
    return jsonify(jobs)


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


# ── Routes: Errored jobs ─────────────────────────────────────────────

@app.route("/api/errored")
@login_required
def api_list_errored():
    jobs = job_scout.list_errored_jobs()
    return jsonify(jobs)


@app.route("/api/errored", methods=["DELETE"])
@login_required
def api_clear_errored():
    count = job_scout.clear_errored_jobs()
    return jsonify({"message": f"Cleared {count} errored jobs"})


@app.route("/api/errored/retry", methods=["POST"])
@login_required
def api_retry_errored():
    email = session.get("email")
    if not email:
        return jsonify({"error": "No user session"}), 401
    try:
        result = job_scout.retry_errored_jobs(email)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Startup ────────────────────────────────────────────────────────────

SEED_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _seed_initial_user():
    """Seed the initial admin user and config from config.yaml if present."""
    if not SEED_CONFIG_PATH.exists():
        return
    with open(SEED_CONFIG_PATH) as f:
        file_cfg = yaml.safe_load(f) or {}
    email = file_cfg.get("user_email")
    if email:
        ensure_user(email, file_cfg.get("user_password", ""), UserPrivilege.ADMIN)
        job_scout.seed_config(email)


def main():
    _ensure_users_collection()
    job_scout._ensure_indexes()
    _seed_initial_user()

    scheduler.start()

    # Restore schedules for all configs that were running before shutdown
    running_configs = job_scout.load_all_running_configs()
    for config in running_configs:
        email = config["email"]
        cron_expr = config.get("schedule", "0 9 * * *")
        _schedule_user(email, cron_expr)
    logger.info("Restored %d running schedules", len(running_configs))

    port = int(os.environ.get("PORT", 6969))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
