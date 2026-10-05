"""Daily snapshot of the cards and prices of every deck in one Archidekt folder.

For each deck directly inside the folder (subfolders are ignored), one row per card is
appended to data/prices.csv, keyed by the deck's Archidekt id and today's date, with a
flag for whether the card has the "Proxied" color tag. Two more files are rewritten each run:
data/decks.csv (one row per deck id: its most recent name and commanders) and
data/cards.csv (one row per card name: type line, mana value and color identity).

Usage:
    python track_prices.py <folder_id>
    (or set the ARCHIDEKT_FOLDER_ID environment variable)

Only public folders/decks can be read, since no login is used.
"""

import csv
import os
import re
import sys
import time
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from requests import Session

API = "https://archidekt.com/api"
SITE = "https://archidekt.com"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CSV_PATH = os.path.join(DATA_DIR, "prices.csv")
DECKS_PATH = os.path.join(DATA_DIR, "decks.csv")
CARDS_PATH = os.path.join(DATA_DIR, "cards.csv")
PROXY_TAG = "proxied"  # color tag name (any capitalization) that marks a card as a proxy

# Archidekt's price keys -> CSV column names
PRICE_SOURCES = {
    "tcg": "price_tcgplayer",
    "ck": "price_cardkingdom",
    "cm": "price_cardmarket",
}

FIELDNAMES = [
    "date",
    "deck_id",
    "card_name",
    "quantity",
    "set_code",
    "finish",
    *PRICE_SOURCES.values(),
    "proxied",
]

DECK_FIELDNAMES = ["deck_id", "deck_name", "commanders", "last_seen"]
CARD_FIELDNAMES = ["card_name", "type_line", "mana_value", "color_identity"]
COLOR_CODES = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}

session = Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (archidekt-price-tracker)"})


def get(url, **kwargs):
    """GET with a few retries, since a daily job shouldn't die on one hiccup."""
    for attempt in range(4):
        response = session.get(url, timeout=30, **kwargs)
        if response.status_code == 429 or response.status_code >= 500:
            time.sleep(2 ** attempt * 5)
            continue
        return response
    return response


# ---------------------------------------------------------------- folder -> deck ids

def deck_ids_from_api(folder_id):
    """Deck ids directly in the folder, via Archidekt's folder API. Returns None if unavailable."""
    url = f"{API}/decks/folders/{folder_id}/"
    ids = []
    while url:
        response = get(url)
        if response.status_code != 200:
            return None
        data = response.json()

        # The folder payload lists its own decks under "decks" (subfolders sit in a
        # separate "subfolders" key, which we deliberately never walk).
        decks = data.get("decks")
        if isinstance(decks, dict):  # paginated shape: {"results": [...], "next": ...}
            url = decks.get("next")
            decks = decks.get("results", [])
        else:
            url = data.get("next")
        if decks is None:
            return None
        ids += [deck["id"] for deck in decks if "id" in deck]
    return ids


def deck_ids_from_page(folder_id):
    """Fallback: read deck links off the public folder page (like LandBase's search scrape).

    The page links to subfolders as /folders/<id>, and to its own decks as /decks/<id>,
    so collecting only /decks/ links keeps us out of subfolders.
    """
    response = get(f"{SITE}/folders/{folder_id}")
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    ids = []
    for a_tag in soup.find_all("a", href=True):
        match = re.match(r"^(?:https?://(?:www\.)?archidekt\.com)?/decks/(\d+)", a_tag["href"])
        if match and int(match.group(1)) not in ids:
            ids.append(int(match.group(1)))
    return ids


def get_deck_ids(folder_id):
    ids = deck_ids_from_api(folder_id)
    if ids is None:
        print("Folder API unavailable, falling back to the folder page.")
        ids = deck_ids_from_page(folder_id)
    return ids


# ---------------------------------------------------------------- deck -> rows

def is_proxied(label):
    """Archidekt stores a card's color tag as "Name,#hexcolor"; only the name matters here."""
    label = label or ""
    name = label.rpartition(",")[0] if "," in label else label
    return name.strip().lower() == PROXY_TAG


def price(prices, key, foil):
    """Use the foil price for foil copies, falling back to nonfoil only if there is none.

    Archidekt names foil prices in lowercase ("tcgfoil", "ckfoil", "cmfoil"); the
    camel-case spelling is checked too in case that ever changes.
    """
    value = None
    if foil:
        value = prices.get(f"{key}foil") or prices.get(f"{key}Foil")
    if not value:
        value = prices.get(key)
    return value if value not in (None, 0, -1, "") else ""


def card_info(oracle):
    """Type line, mana value and color identity (WUBRG letters) from Archidekt's card data."""
    types = " ".join((oracle.get("superTypes") or []) + (oracle.get("types") or []))
    subtypes = " ".join(oracle.get("subTypes") or [])
    identity = {COLOR_CODES.get(str(c).lower(), str(c)[:1].upper()) for c in oracle.get("colorIdentity") or []}
    cmc = oracle.get("cmc")
    return {
        "card_name": oracle.get("name", ""),
        "type_line": f"{types} — {subtypes}" if subtypes else types,
        "mana_value": int(cmc) if isinstance(cmc, (int, float)) and cmc == int(cmc) else (cmc if cmc is not None else ""),
        "color_identity": "".join(c for c in "WUBRG" if c in identity),
    }


def fetch_deck(deck_id, today):
    """Returns (deck name, commander names, card rows, card info) for one deck."""
    response = get(f"{API}/decks/{deck_id}/")
    response.raise_for_status()
    deck = response.json()

    # Categories the deck excludes from the deck itself (Maybeboard, Sideboard, and any
    # custom category with "included in deck" turned off).
    excluded = {
        category.get("name")
        for category in deck.get("categories") or []
        if category.get("includedInDeck") is False
    }
    # The commander zone is Archidekt's "premier" category (normally called "Commander").
    premier = {category.get("name") for category in deck.get("categories") or [] if category.get("isPremier")} or {"Commander"}

    rows, commanders, infos = [], [], []
    for entry in deck.get("cards", []):
        # A card's primary category is the first one listed; uncategorized cards count.
        categories = entry.get("categories") or []
        if categories and categories[0] in excluded:
            continue

        card = entry.get("card", {})
        oracle = card.get("oracleCard", {})
        infos.append(card_info(oracle))
        if categories and categories[0] in premier:
            commanders.append(oracle.get("name", ""))
        foil = (entry.get("modifier") or "").lower() == "foil"
        prices = card.get("prices") or {}
        row = {
            "date": today,
            "deck_id": deck_id,
            "card_name": card.get("oracleCard", {}).get("name", ""),
            "quantity": entry.get("quantity", 1),
            "set_code": (card.get("edition") or {}).get("editioncode", ""),
            "finish": entry.get("modifier") or "Normal",
            "proxied": "true" if is_proxied(entry.get("label")) else "false",
        }
        for key, column in PRICE_SOURCES.items():
            row[column] = price(prices, key, foil)
        rows.append(row)
    return deck.get("name", ""), commanders, rows, infos


# ---------------------------------------------------------------- csv

def write_rows(new_rows, today):
    """Append today's rows, replacing any rows already written today (safe to re-run)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    existing = []
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            existing = [row for row in csv.DictReader(f) if row.get("date") != today]

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing)
        writer.writerows(new_rows)


def write_deck_names(names, commanders, today):
    """Keep one row per deck id with its latest name and commanders. Decks that leave the
    folder keep their last known details, so their history still has a label."""
    os.makedirs(DATA_DIR, exist_ok=True)
    decks = {}
    if os.path.exists(DECKS_PATH):
        with open(DECKS_PATH, newline="", encoding="utf-8") as f:
            decks = {row["deck_id"]: row for row in csv.DictReader(f)}
    for deck_id, name in names.items():
        decks[str(deck_id)] = {"deck_id": deck_id, "deck_name": name,
                               "commanders": " | ".join(commanders.get(deck_id, [])), "last_seen": today}

    with open(DECKS_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DECK_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(decks.values(), key=lambda d: int(d["deck_id"])))


def write_card_info(infos):
    """One row per card name. Cards seen this run are refreshed; older ones are kept."""
    os.makedirs(DATA_DIR, exist_ok=True)
    cards = {}
    if os.path.exists(CARDS_PATH):
        with open(CARDS_PATH, newline="", encoding="utf-8") as f:
            cards = {row["card_name"]: row for row in csv.DictReader(f)}
    for info in infos:
        if info["card_name"]:
            cards[info["card_name"]] = info

    with open(CARDS_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CARD_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(cards.values(), key=lambda c: c["card_name"]))


def main():
    folder_id = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ARCHIDEKT_FOLDER_ID", "")).strip()
    folder_id = re.sub(r"\D", "", folder_id.rstrip("/").split("/")[-1])  # accept a URL too
    if not folder_id:
        sys.exit("Give a folder id: python track_prices.py <folder_id> (or set ARCHIDEKT_FOLDER_ID)")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    deck_ids = get_deck_ids(folder_id)
    print(f"Folder {folder_id}: {len(deck_ids)} deck(s): {deck_ids}")
    if not deck_ids:
        sys.exit("No decks found. Is the folder public, and are the decks directly inside it?")

    rows, names, commanders, infos, failed = [], {}, {}, [], []
    for deck_id in deck_ids:
        try:
            name, deck_commanders, these, these_infos = fetch_deck(deck_id, today)
            print(f"  deck {deck_id} ({name}): {len(these)} card entries")
            names[deck_id] = name
            commanders[deck_id] = deck_commanders
            rows += these
            infos += these_infos
        except Exception as error:  # one private/broken deck shouldn't sink the run
            print(f"  deck {deck_id}: FAILED ({error})")
            failed.append(deck_id)
        time.sleep(1)  # be polite to Archidekt

    write_rows(rows, today)
    write_deck_names(names, commanders, today)
    write_card_info(infos)
    print(f"Wrote {len(rows)} rows for {today}, {len(names)} decks, {len(infos)} card details")
    if failed and len(failed) == len(deck_ids):
        sys.exit("Every deck failed to load.")


if __name__ == "__main__":
    main()
