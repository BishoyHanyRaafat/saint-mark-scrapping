# pip install beautifulsoup4 requests python-dotenv --break-system-packages
import os
import sys

import requests
from bs4 import BeautifulSoup
import time
import traceback

fog_number = int(os.environ.get("FOG_NUMBER", "6") or "6")

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
REGISTRY_MESSAGE_ID = os.environ.get("REGISTRY_MESSAGE_ID", "").strip()

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    )
}


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
        sent = telegram_api("sendMessage", chat_id=CHAT_ID, text="# registry\n")
        new_id = sent["result"]["message_id"]
        telegram_api(
            "sendMessage",
            chat_id=CHAT_ID,
            text=(
                f"� Registry message created (id={new_id}). "
                "Edit it in Telegram to add people, then set "
                f"REGISTRY_MESSAGE_ID={new_id} in GitHub Actions variables."
            ),
        )
        return ""

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


raw_people = fetch_registry_text().strip().splitlines()
people = []
for line in raw_people:
    parts = line.split("|")
    if len(parts) < 7:
        print(f"Skipping malformed PEOPLE line: {line!r}")
        continue
    people.append(
        {
            "name": parts[0],
            "osra": parts[1],
            "transport": parts[2],
            "national_id": parts[3],
            "phone": parts[4],
            "statues": parts[5],
            "notes": parts[6],
        }
    )

print(f"Loaded {len(people)} people from environment variable.")

reg_url = f"https://mo2tmar-5edma.stmarkos.org/fog_registration_form?TravellerType=servent&fogNumber={fog_number}"
check_url = "https://mo2tmar-5edma.stmarkos.org/"


def check_availability():
    response = requests.get(check_url, headers=headers)

    print(response.status_code)
    print(response.text[:500])
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    table = soup.find("table")

    if not table:
        raise ValueError("No table found in the HTML content.")

    fog_map_number = {
        1: "الفوج الاول",
        2: "الفوج الثاني",
        3: "الفوج الثالث",
        4: "الفوج الرابع",
        5: "الفوج الخامس",
        6: "الفوج السادس",
        7: "الفوج السابع",
    }

    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if (
            tds
            and tds[0].text.strip() == fog_map_number[fog_number]
            and tds[1].text.strip() == "يوجد اماكن"
        ):
            return True
    return False


def send_registration_request(person):
    session = requests.Session()

    response = session.get(reg_url, headers=headers)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    token = soup.find("input", {"name": "_token"})["value"]
    from_options = [
        option for option in soup.select("#fromDate option") if option.get("value")
    ]
    to_options = [
        option for option in soup.select("#toDate option") if option.get("value")
    ]

    from_date = from_options[0]["value"]
    to_date = to_options[-1]["value"]

    print("from:", from_date)
    print("to:", to_date)

    data = {
        "_token": token,
        "fogNumber": str(fog_number),
        "fromDate": from_date,
        "toDate": to_date,
        "transport": person["transport"],
        "name": person["name"],
        "nationalId": person["national_id"],
        "phone": person["phone"],
        "statues": person["statues"],
        "osra": person["osra"],
        "notes": person["notes"],
        "brotherAndSisterName": "",
        "brotherAndSisterNationalId": "",
        "brotherAndSisterPhone": "",
        "brotherAndSisterNotes": "",
        "engagedName": "",
        "engagedNationalId": "",
        "engagedPhone": "",
        "engagedNotes": "",
        "marriedName": "",
        "marriedNationalId": "",
        "marriedPhone": "",
        "marriedNotes": "",
        "childrenLessThan2Count": "0",
        "childrenLessThan8Count": "0",
        "childrenMoreThan8Count": "0",
        "childrenAgesField": "",
        "familyName": "",
        "familyNationalId": "",
        "familyPhone": "",
        "familyNotes": "",
    }

    response = session.post(
        reg_url,
        data=data,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": reg_url,
        },
    )

    return response


def send_message(message: str):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": message},
    )


def edit_registry_message():
    if not REGISTRY_MESSAGE_ID:
        return
    body = "# registry\n"
    for p in people:
        body += (
            f"{p['name']}|{p['osra']}|{p['transport']}|"
            f"{p['national_id']}|{p['phone']}|{p['statues']}|{p['notes']}\n"
        )
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

        print(response.status_code)
        print(response.url)
        print(response.text[:1000])

        send_message(
            f"Registration response for {person['name']}: "
            f"{response.status_code} - {response.url}\n"
            f"{response.text[:1000]}"
        )

        if response.status_code == 200:
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
