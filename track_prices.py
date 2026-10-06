"""Daily snapshot of the cards and prices of every deck in one Archidekt folder.

Only decks directly inside the folder are read (subfolders are ignored). Decks are always
identified by their Archidekt deck id. Each run writes:

  data/latest.csv          today's cards in every deck, with prices and a proxy flag
  data/totals.csv          one row per deck per day: card count and total/paper/proxy value
                           at each store (what the charts draw)
  data/decklists.csv       a change log of deck contents: a deck's full list the first day
                           it's seen, then only the entries that changed (quantity 0 = removed)
  data/card_prices/YYYY-MM.csv
                           one price row per printing per day, shared by every deck
  data/decks.csv           one row per deck: latest name, commanders, first and last seen
  data/cards.csv           one row per card name: type line, mana value, color identity

A deck missing from the folder for PRUNE_AFTER_DAYS days has all of its history deleted.

Usage:
    python track_prices.py <folder_id>
    (or set the ARCHIDEKT_FOLDER_ID environment variable)

Only public folders/decks can be read, since no login is used.
"""

import csv
import glob
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from requests import RequestException, Session

API = "https://archidekt.com/api"
SITE = "https://archidekt.com"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LATEST_PATH = os.path.join(DATA_DIR, "latest.csv")
TOTALS_PATH = os.path.join(DATA_DIR, "totals.csv")
DECKLISTS_PATH = os.path.join(DATA_DIR, "decklists.csv")
CARD_PRICES_DIR = os.path.join(DATA_DIR, "card_prices")
DECKS_PATH = os.path.join(DATA_DIR, "decks.csv")
CARDS_PATH = os.path.join(DATA_DIR, "cards.csv")
LEGACY_PRICES_PATH = os.path.join(DATA_DIR, "prices.csv")  # old single-file format, migrated on sight
PRUNE_AFTER_DAYS = 10
TIMEZONE = ZoneInfo("America/Denver")  # snapshot dates are Mountain time
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

DECK_FIELDNAMES = ["deck_id", "deck_name", "commanders", "first_seen", "last_seen"]
TOTAL_FIELDNAMES = ["date", "deck_id", "cards"] + [
    f"{src}_{part}" for src in ("tcg", "ck", "cm") for part in ("total", "paper", "proxy")]
SOURCE_COLUMNS = {"tcg": "price_tcgplayer", "ck": "price_cardkingdom", "cm": "price_cardmarket"}
DECKLIST_FIELDNAMES = ["date", "deck_id", "card_name", "set_code", "finish", "proxied", "quantity"]
CARD_PRICE_FIELDNAMES = ["date", "card_name", "set_code", "finish", *PRICE_SOURCES.values()]
CARD_FIELDNAMES = ["card_name", "type_line", "mana_value", "color_identity"]
COLOR_CODES = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}

session = Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (archidekt-price-tracker)"})


def get(url, **kwargs):
    """GET with retries, since a daily job shouldn't die on one hiccup. Retries rate limits,
    server errors and dropped connections, waiting 5s, 10s, 20s, 40s between tries."""
    for attempt in range(5):
        try:
            response = session.get(url, timeout=30, **kwargs)
        except RequestException as error:
            if attempt == 4:
                raise
            print(f"  retrying {url} after {type(error).__name__}")
            time.sleep(2 ** attempt * 5)
            continue
        if (response.status_code == 429 or response.status_code >= 500) and attempt < 4:
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


# Printed order of card types, e.g. "Artifact Creature", "Enchantment Land", "Kindred Instant".
TYPE_ORDER = ["Kindred", "Tribal", "Artifact", "Enchantment", "Land", "Planeswalker", "Battle", "Creature", "Instant", "Sorcery"]


def type_order(card_type):
    return TYPE_ORDER.index(card_type) if card_type in TYPE_ORDER else len(TYPE_ORDER)


def card_info(oracle):
    """Type line, mana value and color identity (WUBRG letters) from Archidekt's card data."""
    types = " ".join((oracle.get("superTypes") or []) + sorted(oracle.get("types") or [], key=type_order))
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


# ---------------------------------------------------------------- csv helpers

def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, fieldnames, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- writers
# Every writer first drops anything already written for `today`, so re-running the job on
# the same day replaces that day's data instead of duplicating it.

def write_latest(rows, failed_ids):
    """Today's rows. A deck that failed to load today keeps its previous rows."""
    keep = [r for r in read_csv(LATEST_PATH) if r["deck_id"] in {str(i) for i in failed_ids}]
    write_csv(LATEST_PATH, FIELDNAMES, keep + rows)


def write_totals(rows, today):
    by_deck = {}
    for r in rows:
        t = by_deck.setdefault(str(r["deck_id"]), {f: 0.0 for f in TOTAL_FIELDNAMES[3:]} | {"cards": 0})
        qty = int(r["quantity"])
        t["cards"] += qty
        part = "proxy" if r["proxied"] == "true" else "paper"
        for src, column in SOURCE_COLUMNS.items():
            value = num(r[column]) * qty
            t[f"{src}_total"] += value
            t[f"{src}_{part}"] += value
    new = [{"date": today, "deck_id": deck_id, **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in t.items()}}
           for deck_id, t in by_deck.items()]
    old = [r for r in read_csv(TOTALS_PATH) if r["date"] != today]
    write_csv(TOTALS_PATH, TOTAL_FIELDNAMES, old + new)


def entry_key(r):
    return (r["card_name"], r["set_code"], r["finish"], r["proxied"])


def write_decklists(rows, today, fetched_ids):
    """Append only what changed in each deck since its previous state."""
    log = [r for r in read_csv(DECKLISTS_PATH) if r["date"] != today]
    state = {}  # deck_id -> {entry key: quantity}
    for r in sorted(log, key=lambda r: r["date"]):
        entries = state.setdefault(r["deck_id"], {})
        if int(r["quantity"]):
            entries[entry_key(r)] = int(r["quantity"])
        else:
            entries.pop(entry_key(r), None)

    today_state = {}
    for r in rows:
        entries = today_state.setdefault(str(r["deck_id"]), {})
        entries[entry_key(r)] = entries.get(entry_key(r), 0) + int(r["quantity"])

    changes = []
    for deck_id in sorted({str(i) for i in fetched_ids}, key=int):  # stable order keeps diffs small
        before, after = state.get(deck_id, {}), today_state.get(deck_id, {})
        for key in sorted(set(before) | set(after)):
            if before.get(key, 0) != after.get(key, 0):
                name, set_code, finish, proxied = key
                changes.append({"date": today, "deck_id": deck_id, "card_name": name, "set_code": set_code,
                                "finish": finish, "proxied": proxied, "quantity": after.get(key, 0)})
    write_csv(DECKLISTS_PATH, DECKLIST_FIELDNAMES, log + changes)
    return len(changes)


def write_card_prices(rows, today):
    """One row per printing for today, in that month's file."""
    seen, new = set(), []
    for r in rows:
        key = (r["card_name"], r["set_code"], r["finish"])
        if key not in seen:
            seen.add(key)
            new.append({"date": today, "card_name": r["card_name"], "set_code": r["set_code"], "finish": r["finish"],
                        **{c: r[c] for c in PRICE_SOURCES.values()}})
    path = os.path.join(CARD_PRICES_DIR, f"{today[:7]}.csv")
    old = [r for r in read_csv(path) if r["date"] != today]
    write_csv(path, CARD_PRICE_FIELDNAMES, old + new)


def write_deck_names(names, commanders, listed_ids, today):
    """One row per deck: latest name and commanders. `last_seen` moves forward whenever the
    deck is listed in the folder, even if fetching it failed that day."""
    decks = {row["deck_id"]: row for row in read_csv(DECKS_PATH)}
    for deck_id in listed_ids:
        row = decks.setdefault(str(deck_id), {"deck_id": deck_id, "deck_name": "", "commanders": "", "first_seen": today})
        row["first_seen"] = row.get("first_seen") or today
        row["last_seen"] = today
        if deck_id in names:
            row["deck_name"] = names[deck_id]
            row["commanders"] = " | ".join(commanders.get(deck_id, []))
    write_csv(DECKS_PATH, DECK_FIELDNAMES, sorted(decks.values(), key=lambda d: int(d["deck_id"])))


def prune_missing_decks(today):
    """Delete every trace of decks not seen in the folder for PRUNE_AFTER_DAYS days."""
    cutoff = (date.fromisoformat(today) - timedelta(days=PRUNE_AFTER_DAYS)).isoformat()
    decks = read_csv(DECKS_PATH)
    gone = {d["deck_id"] for d in decks if d.get("last_seen", today) <= cutoff}
    if not gone:
        return []
    write_csv(DECKS_PATH, DECK_FIELDNAMES, [d for d in decks if d["deck_id"] not in gone])
    for path, fields in ((TOTALS_PATH, TOTAL_FIELDNAMES), (DECKLISTS_PATH, DECKLIST_FIELDNAMES), (LATEST_PATH, FIELDNAMES)):
        write_csv(path, fields, [r for r in read_csv(path) if r["deck_id"] not in gone])
    return sorted(gone)


def write_card_info(infos):
    """One row per card name. Cards seen this run are refreshed; older ones are kept."""
    cards = {row["card_name"]: row for row in read_csv(CARDS_PATH)}
    for info in infos:
        if info["card_name"]:
            cards[info["card_name"]] = info
    write_csv(CARDS_PATH, CARD_FIELDNAMES, sorted(cards.values(), key=lambda c: c["card_name"]))


def migrate_legacy_prices():
    """Convert the old all-in-one data/prices.csv into the current files, then remove it."""
    old = read_csv(LEGACY_PRICES_PATH)
    if not old:
        return
    dates = sorted({r["date"] for r in old})
    for day in dates:
        rows = [r for r in old if r["date"] == day]
        ids = {r["deck_id"] for r in rows}
        write_totals(rows, day)
        write_decklists(rows, day, ids)
        write_card_prices(rows, day)
    write_csv(LATEST_PATH, FIELDNAMES, [r for r in old if r["date"] == dates[-1]])
    decks = {d["deck_id"]: d for d in read_csv(DECKS_PATH)}
    for d in decks.values():
        seen = [r["date"] for r in old if r["deck_id"] == d["deck_id"]]
        d["first_seen"] = d.get("first_seen") or (min(seen) if seen else d.get("last_seen", ""))
    write_csv(DECKS_PATH, DECK_FIELDNAMES, list(decks.values()))
    os.remove(LEGACY_PRICES_PATH)
    print(f"Migrated {len(old)} rows from prices.csv ({len(dates)} day(s))")


def main():
    folder_id = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ARCHIDEKT_FOLDER_ID", "")).strip()
    folder_id = re.sub(r"\D", "", folder_id.rstrip("/").split("/")[-1])  # accept a URL too
    if not folder_id:
        sys.exit("Give a folder id: python track_prices.py <folder_id> (or set ARCHIDEKT_FOLDER_ID)")

    today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    deck_ids = get_deck_ids(folder_id)
    print(f"Folder {folder_id}: {len(deck_ids)} deck(s): {deck_ids}")
    if not deck_ids:
        sys.exit("No decks found. Is the folder public, and are the decks directly inside it?")

    migrate_legacy_prices()

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

    if failed and len(failed) == len(deck_ids):
        sys.exit("Every deck failed to load; nothing written.")

    write_latest(rows, failed)
    write_totals(rows, today)
    changed = write_decklists(rows, today, names.keys())
    write_card_prices(rows, today)
    write_deck_names(names, commanders, deck_ids, today)
    write_card_info(infos)
    pruned = prune_missing_decks(today)
    print(f"{today}: {len(rows)} card rows from {len(names)} decks, {changed} decklist changes"
          + (f", deleted history for decks {pruned}" if pruned else ""))


if __name__ == "__main__":
    main()
