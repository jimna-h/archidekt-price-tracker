# Archidekt Snapshot

A GitHub Action that, once a day, snapshots every deck in one [Archidekt](https://archidekt.com) folder: the cards in each deck, their current prices, and whether each card is a proxy. It builds up a price and decklist history over time (see [Data files](#data-files)).

`index.html` (Archidekt Snapshot) has four tabs:
- **Decks**: each deck's value over time (total, paper and proxies), biggest price movers, card swaps, mana curve and card types, and the decklist on any past date. Includes a whole-collection view and an average-deck view.
- **Proxies**: every proxied card, its price at TCGplayer, Card Kingdom and Cardmarket, the cheapest option, and a running total of what it would cost to replace them.
- **Deck cards**: a Magic-card-sized card for each deck (color-identity frame, art, bracket, Game Changers, combos, tutors and a QR code to the deck), laid out to print nine to a letter page and sleeve with the deck.
- **Search all decks**: find any card across every deck and see which decks run it, plus a grid of how many cards each pair of decks shares. (This replaces the old [edh-ledger](https://github.com/jimna-h/edh-ledger) project.)

The page address always reflects the tab, deck and filters you have open, so you can bookmark or share any view (there's a "Copy link" button too).

Turn on GitHub Pages (Settings → Pages → Deploy from a branch → `main`, `/ (root)`) to view it online.

Builds on the Archidekt API approach from [LandBase](https://github.com/jimna-h/LandBase).

## Setup

1. Find your folder's id — it's the number in the folder URL, e.g. `https://archidekt.com/folders/781767` → `781767`. The folder and its decks must be **public**.
2. In this repo on GitHub: **Settings → Secrets and variables → Actions → Variables → New repository variable**, name `ARCHIDEKT_FOLDER_ID`, value your folder id.
3. **Settings → Actions → General → Workflow permissions**: choose **Read and write permissions** (so the action can commit the CSV).
4. Run it once by hand from the **Actions** tab ("Daily snapshot" → *Run workflow*) to check it works. After that it runs every morning (13:17 UTC, which is 7:17am Mountain in summer and 6:17am in winter), with backup runs later in the day in case GitHub skips the scheduled one. Snapshot dates are Mountain time.

Only decks directly inside the folder are tracked; subfolders are ignored.

Within each deck, a card is only recorded if its primary (first) category is included in the deck, so Maybeboard, Sideboard and any custom category with "included in deck" turned off are skipped. Uncategorized cards are recorded.

## Data files

Decks are always identified by their Archidekt deck id. The files are laid out so the page only downloads a couple of megabytes however long this runs.

| File | What's in it | Size over time |
|---|---|---|
| `data/latest.csv` | Today's cards in every deck: `deck_id`, `card_name`, `quantity`, `set_code`, `finish`, the three prices, `proxied` | Rewritten daily, stays ~60 KB |
| `data/totals.csv` | One row per deck per day: card count, and total / paper / proxy value at each store (`tcg_*`, `ck_*`, `cm_*`) | ~1 KB a day |
| `data/decklists.csv` | Change log of deck contents. A deck's full list on its first day, then only entries whose quantity changed (`0` = removed). Swaps and decklist history are rebuilt from this | Grows only when you change decks |
| `data/card_prices/YYYY-MM.csv` | One row per printing per day with the three prices, shared across decks | ~1.3 MB a month; the page loads the last two months |
| `data/decks.csv` | One row per deck: latest name, commanders, the Scryfall id of each commander's printing (for its art), first and last seen | Tiny |
| `data/decks.csv` (continued) | Also each deck's Archidekt bracket, owner, custom featured art, and the cards behind its bracket: Game Changers, nonland tutors, mass land denial, extra turns, and complete two-card combos with their total mana value | |
| `data/folder.csv` | The Archidekt folder id, for the page's folder link | Tiny |
| `data/cards.csv` | One row per card name: type line, mana value, color identity | Tiny |

Prices are Archidekt's: TCGplayer, Card Kingdom and Cardmarket, using the foil price for foil copies. A deck that disappears from the folder for 10 days has all of its history deleted.

Re-running on the same day replaces that day's rows rather than duplicating them.

## Running locally

```bash
pip install -r requirements.txt
python track_prices.py 781767
```
