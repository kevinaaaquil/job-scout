import os
import logging

from pymongo import MongoClient

logger = logging.getLogger("db")

_client = None
_db = None


def get_db():
    global _client, _db
    if _db is not None:
        return _db

    uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    db_name = os.environ.get("MONGO_DB", "jobscout")

    _client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    _db = _client[db_name]
    logger.info("Connected to MongoDB: %s / %s", uri.split("@")[-1], db_name)
    return _db
