import copy
import logging
import os
import threading
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, jsonify, request, send_from_directory

import job_scout

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
logger = logging.getLogger("server")

IST = ZoneInfo("Asia/Kolkata")

app = Flask(__name__, static_folder="static")
scheduler = BackgroundScheduler(daemon=True, timezone=IST)

JOB_ID = "job_scout_daily"
_current_run_lock = threading.Lock()
_current_run_status = {"running": False, "stage": "", "detail": ""}


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


# ── Routes: Static ─────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ── Routes: Status ─────────────────────────────────────────────────────

@app.route("/api/status")
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
def api_run_detail(run_id):
    doc = job_scout.get_run_by_id(run_id)
    if doc:
        return jsonify(doc)
    return jsonify({"error": "Run not found"}), 404


@app.route("/api/run", methods=["POST"])
def api_trigger_run():
    with _current_run_lock:
        if _current_run_status["running"]:
            return jsonify({"error": "A run is already in progress"}), 409

    thread = threading.Thread(target=_execute_run, daemon=True)
    thread.start()
    return jsonify({"message": "Run triggered", "status": "started"})


# ── Routes: Debug ──────────────────────────────────────────────────────

@app.route("/api/debug/search", methods=["POST"])
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
def api_debug_queries():
    """Show the fully rendered queries that would be used in a run."""
    config = job_scout.load_config()
    queries = job_scout.build_queries(config)
    return jsonify(queries)


# ── Routes: Config ─────────────────────────────────────────────────────

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
def api_get_config():
    config = job_scout.load_config()
    return jsonify(_enrich_config_for_api(config))


@app.route("/api/config", methods=["PUT"])
def api_update_config():
    current = job_scout.load_config()
    updates = request.get_json()

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
def api_get_schedule():
    config = job_scout.load_config()
    return jsonify({
        "schedule": config.get("schedule", "0 9 * * *"),
        "next_run": _get_next_run(),
    })


@app.route("/api/config/schedule", methods=["PUT"])
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


# ── Startup ────────────────────────────────────────────────────────────

def main():
    config = job_scout.load_config()
    cron_expr = config.get("schedule", "0 9 * * *")

    scheduler.start()
    _reschedule(cron_expr)
    logger.info("Server starting. Next run: %s", _get_next_run())

    port = int(os.environ.get("PORT", 6969))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
