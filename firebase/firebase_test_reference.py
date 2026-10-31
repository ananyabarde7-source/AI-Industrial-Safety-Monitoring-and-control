"""Reference Firebase REST test; original exact test script was not attached."""
import json
from datetime import datetime, timezone
from urllib.parse import quote
import requests

with open("config.json", "r", encoding="utf-8") as f:
    cfg = json.load(f)

api_key = cfg["firebase_api_key"]
database_url = cfg["database_url"].rstrip("/")
email = cfg["firebase_email"]
password = cfg["firebase_password"]
path_template = cfg.get("database_path", "users/{uid}/events")

auth = requests.post(
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
    params={"key": api_key},
    json={"email": email, "password": password, "returnSecureToken": True},
    timeout=15,
)
auth.raise_for_status()
auth_data = auth.json()
uid = auth_data["localId"]
id_token = auth_data["idToken"]
print("Firebase authentication: OK")
print("Firebase UID: loaded")

path = path_template.format(uid=quote(uid, safe=""))
url = "{}/{}.json".format(database_url, path.strip("/"))
event = {
    "type": "firebase_connection_test",
    "message": "Test event uploaded successfully",
    "created_at": datetime.now(timezone.utc).isoformat(),
}
response = requests.post(url, params={"auth": id_token}, json=event, timeout=15)
response.raise_for_status()
print("Firebase upload: OK")
print("Test event uploaded successfully.")
