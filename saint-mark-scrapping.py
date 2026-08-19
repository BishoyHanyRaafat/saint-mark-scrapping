# pip install requests python-dotenv --break-system-packages
import os
import sys
import json

import requests
import time
import traceback

fog_number = int(os.environ.get("FOG_NUMBER", "6") or "6")

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
REGISTRY_MESSAGE_ID = os.environ.get("REGISTRY_MESSAGE_ID", "").strip()
WORKER_URL = os.environ["WORKER_URL"].rstrip("/")
WORKER_SECRET = os.environ["WORKER_SECRET"]


def telegram_api(method, **fields):
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        data=fields,
        timeout=20,
    )
    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API {method} failed: {result}")

    return result


def fetch_registry_text():
    """Read the registry message via forwardMessage+deleteMessage trick,
    since the Bot API has no getMessage(message_id). Returns the message text,
    or an empty string if the message hasn't been seeded yet (in which case
    a fresh empty registry is created and the user is told its new id)."""
    if not REGISTRY_MESSAGE_ID:
        sent = telegram_api("sendMessage", chat_id=CHAT_ID, text="[]")
        new_id = sent["result"]["message_id"]
        telegram_api(
            "sendMessage",
            chat_id=CHAT_ID,
            text=(
                f" Registry message created (id={new_id}). "
                "Edit it in Telegram to add people, then set "
                f"REGISTRY_MESSAGE_ID={new_id} in GitHub Actions variables."
            ),
        )
        return "[]"

    fwd = telegram_api(
        "forwardMessage",
        chat_id=CHAT_ID,
        from_chat_id=CHAT_ID,
        message_id=REGISTRY_MESSAGE_ID,
    )
    forwarded_id = fwd["result"]["message_id"]
    text = fwd["result"].get("text", "")
    telegram_api("deleteMessage", chat_id=CHAT_ID, message_id=forwarded_id)
    return text


REQUIRED_FIELDS = (
    "name",
    "osra",
    "transport",
    "national_id",
    "phone",
    "statues",
    "notes",
)


def load_people():
    text = fetch_registry_text().strip() or "[]"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Registry message is not valid JSON: {e}")
    if not isinstance(data, list):
        raise RuntimeError("Registry must be a JSON array of person objects.")
    people = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            print(f"Skipping non-object registry entry #{i}: {entry!r}")
            continue
        person = {field: str(entry.get(field, "")) for field in REQUIRED_FIELDS}
        people.append(person)
    return people


people = load_people()
print(f"Loaded {len(people)} people from registry message.")


def worker_call(path, payload):
    response = requests.post(
        f"{WORKER_URL}{path}",
        json=payload,
        headers={"X-Worker-Secret": WORKER_SECRET},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def check_availability():
    result = worker_call("/check", {"fogNumber": fog_number})
    return bool(result.get("available"))


def send_registration_request(person):
    result = worker_call(
        "/register",
        {"fogNumber": fog_number, "person": person},
    )
    if "error" in result:
        raise RuntimeError(f"Worker error: {result['error']} ({result.get('detail')})")
    return result


def send_message(message: str):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": message},
    )


def edit_registry_message():
    if not REGISTRY_MESSAGE_ID:
        return
    body = json.dumps(people, ensure_ascii=False, separators=(",", ":"))
    telegram_api(
        "editMessageText",
        chat_id=CHAT_ID,
        message_id=REGISTRY_MESSAGE_ID,
        text=body,
    )


def main():
    print(f"Checking availability for Fog {fog_number}...")
    available = check_availability()

    if not available:
        print(f"Fog {fog_number} is not available yet.")
        send_message(f"Fog {fog_number} is not available yet. Will check again later.")
        return 0

    send_message(f"Fog {fog_number} is available! Registering now...")

    i = 0
    while i < len(people):
        person = people[i]
        try:
            response = send_registration_request(person)
        except Exception as e:
            send_message(
                f"Error during registration for {person['name']}: {e}\n"
                f"{traceback.format_exc()}"
            )
            return 1

        print(response)
        send_message(
            f"Registration response for {person['name']}: "
            f"{response.get('status')} - {response.get('url')}\n"
            f"{response.get('body', '')[:3000]}"
        )

        if response.get("status") == 200:
            registered = people.pop(i)
            edit_registry_message()
            send_message(
                f"✅ {registered['name']} was registered and removed from the list."
            )
        else:
            print(f"Registration failed for {person['name']}, keeping on list.")
            i += 1

        time.sleep(5)

        if not check_availability():
            break

    if not people:
        send_message("✅ All people have been registered.")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        send_message(f"Fatal error: {e}\n{traceback.format_exc()}")
        raise
