# Archidekt Price Tracker

A GitHub Action that, once a day, snapshots every deck in one [Archidekt](https://archidekt.com) folder: the cards in each deck, their current prices, and their color tags. Rows are appended to `data/prices.csv`, so the file builds up a price history over time.

Builds on the Archidekt API approach from [LandBase](https://github.com/jimna-h/LandBase).

## Setup

1. Find your folder's id — it's the number in the folder URL, e.g. `https://archidekt.com/folders/781767` → `781767`. The folder and its decks must be **public**.
2. In this repo on GitHub: **Settings → Secrets and variables → Actions → Variables → New repository variable**, name `ARCHIDEKT_FOLDER_ID`, value your folder id.
3. **Settings → Actions → General → Workflow permissions**: choose **Read and write permissions** (so the action can commit the CSV).
4. Run it once by hand from the **Actions** tab ("Daily deck prices" → *Run workflow*) to check it works. After that it runs daily at 13:17 UTC.

Only decks directly inside the folder are tracked; subfolders are ignored.

Within each deck, a card is only recorded if its primary (first) category is included in the deck, so Maybeboard, Sideboard and any custom category with "included in deck" turned off are skipped. Uncategorized cards are recorded.

## CSV columns

| Column | Meaning |
|---|---|
| `date` | UTC date of the snapshot |
| `deck_id` | Archidekt deck id (the deck's identifier) |
| `deck_name` | Deck name at snapshot time (for readability; can change) |
| `card_name` | Card name |
| `quantity` | Copies in the deck |
| `set_code` | Printing's set code |
| `finish` | `Normal`, `Foil`, `Etched`… |
| `price_tcgplayer`, `price_cardkingdom`, `price_cardmarket` | Archidekt's listed price per copy (foil price for foil copies); blank if none |
| `color_tag` | The card's Archidekt color tag name (blank if untagged or unnamed) |
| `color_tag_hex` | The color tag's color, e.g. `#37d67a` |
| `categories` | The card's deck categories, `; `-separated |

Re-running on the same day replaces that day's rows rather than duplicating them.

## Running locally

```bash
pip install -r requirements.txt
python track_prices.py 781767
```
