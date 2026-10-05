# Archidekt Snapshot

A GitHub Action that, once a day, snapshots every deck in one [Archidekt](https://archidekt.com) folder: the cards in each deck, their current prices, and whether each card is a proxy. Rows are appended to `data/prices.csv`, so the file builds up a price history over time. Deck names and commanders are kept in `data/decks.csv`, and each card's type, mana value and color identity in `data/cards.csv`.

`index.html` (Archidekt Snapshot) has two tabs:
- **Decks**: each deck's value over time (total, paper and proxies), card swaps between snapshots, plus a whole-collection view and an average-deck view.
- **Search all decks**: find any card across every deck in the latest snapshot and see which decks run it, with which decks run it. Filter by color identity, type, subtype, deck, commanders or proxies. (This replaces the old [edh-ledger](https://github.com/jimna-h/edh-ledger) project, reading the daily snapshot instead of manual imports.)

Turn on GitHub Pages (Settings → Pages → Deploy from a branch → `main`, `/ (root)`) to view it online.

Builds on the Archidekt API approach from [LandBase](https://github.com/jimna-h/LandBase).

## Setup

1. Find your folder's id — it's the number in the folder URL, e.g. `https://archidekt.com/folders/781767` → `781767`. The folder and its decks must be **public**.
2. In this repo on GitHub: **Settings → Secrets and variables → Actions → Variables → New repository variable**, name `ARCHIDEKT_FOLDER_ID`, value your folder id.
3. **Settings → Actions → General → Workflow permissions**: choose **Read and write permissions** (so the action can commit the CSV).
4. Run it once by hand from the **Actions** tab ("Daily snapshot" → *Run workflow*) to check it works. After that it runs daily at 13:17 UTC.

Only decks directly inside the folder are tracked; subfolders are ignored.

Within each deck, a card is only recorded if its primary (first) category is included in the deck, so Maybeboard, Sideboard and any custom category with "included in deck" turned off are skipped. Uncategorized cards are recorded.

## Data files

Decks are always identified by their Archidekt deck id.

**`data/prices.csv`**: one row per card per deck per day.

| Column | Meaning |
|---|---|
| `date` | UTC date of the snapshot |
| `deck_id` | Archidekt deck id |
| `card_name` | Card name |
| `quantity` | Copies in the deck |
| `set_code` | Printing's set code |
| `finish` | `Normal`, `Foil`, `Etched`… |
| `price_tcgplayer`, `price_cardkingdom`, `price_cardmarket` | Archidekt's listed price per copy (foil price for foil copies); blank if none |
| `proxied` | `true` if the card has the "Proxied" color tag on Archidekt, else `false` |

**`data/decks.csv`**: one row per deck id.

| Column | Meaning |
|---|---|
| `deck_id` | Archidekt deck id |
| `deck_name` | The deck's most recent name |
| `commanders` | Cards in the deck's commander zone (Archidekt's premier category), separated by ` \| ` |
| `last_seen` | Last date the deck was found in the folder |

**`data/cards.csv`**: one row per card name, refreshed each run.

| Column | Meaning |
|---|---|
| `card_name` | Card name |
| `type_line` | e.g. `Legendary Artifact Creature — Human Soldier` (front face for double-faced cards) |
| `mana_value` | Mana value |
| `color_identity` | WUBRG letters, blank for colorless |

Re-running on the same day replaces that day's rows rather than duplicating them.

## Running locally

```bash
pip install -r requirements.txt
python track_prices.py 781767
```
