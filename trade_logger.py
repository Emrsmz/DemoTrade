#!/usr/bin/env python3
"""
================================================================================
 TRADE LOGGER  -  RECORDS DISPLAYED SIGNALS IN AN .ods SPREADSHEET
================================================================================

This module records every signal shown by the analyzer into an OpenDocument
spreadsheet (.ods), using the exact column layout of the user's Template.ods:

    Trade | Stock Name | To | Başlangıç Fiyatı | Kapanış Fiyatı |
    Açılış Saat | Kap Saat | Pozisyon | Tarih | Result | Signal

How each column is filled:
  * Trade            - sequential trade number, continued from the file.
  * Stock Name       - the coin's base asset (e.g. UNI from UNI/USDT).
  * To               - the quote asset (e.g. USDT).
  * Başlangıç Fiyatı - entry price (the live price at the moment of the scan).
  * Kapanış Fiyatı   - close price. Filled AUTOMATICALLY once the suggested
                       exit time (Kap Saat) has passed, using the market price
                       at that moment. Left blank until then.
  * Açılış Saat      - open/entry time (now).
  * Kap Saat         - suggested close time (now + the tier's hold window).
  * Tarih            - the date (DD/MM/YYYY).
  * Result           - filled automatically together with the close price:
                       "WIN +x.xx%" or "LOSS -x.xx%" relative to the entry.
  * Signal           - the number of agreeing indicators (1-5).
  * Signal Detail    - the measured value behind each agreeing indicator
                       (e.g. "RSI 24 | MACD 0.12% | BB 0.30% | VOL 3.2x"), so
                       the strength of every signal can be reviewed later. This
                       is one extra column appended after the template layout.
  * Pozisyon         - the trade direction as text ("Long" for BUY, "Short" for
                       SELL). Long is coloured green and Short red, matching the
                       coin-name colour.
  * Trend (1s)       - the aligned higher-timeframe (1-hour) trend at signal
                       time ("UP", "DOWN" or "NEUTRAL"), the last column.

The coin name is also coloured by direction (green = Long/BUY, red =
Short/SELL); the win/loss maths reads the direction from the Pozisyon column
when present, and falls back to the coin-name colour for older files.

The whole file is regenerated on every write, so the styling (coloured header,
centred cells, column widths, Long/Short and WIN/LOSS colours) stays
consistent. To avoid flooding the log when the board refreshes every 60
seconds, a coin is not logged again while it still has an open position.
================================================================================
"""

import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta

try:
    import fcntl  # POSIX advisory locking; absent on non-POSIX platforms
except ImportError:  # pragma: no cover
    fcntl = None

from odf.opendocument import OpenDocumentSpreadsheet, load
from odf.style import (
    ParagraphProperties,
    Style,
    TableCellProperties,
    TableColumnProperties,
    TextProperties,
)
from odf.table import Table, TableColumn, TableCell, TableRow
from odf.text import P

# Column headers, in order, copied verbatim from the user's Template.ods so the
# generated file matches that format exactly.
HEADERS = [
    "Trade",
    "Stock Name",
    "To",
    "Başlangıç Fiyatı",
    "Kapanış Fiyatı",
    "Açılış Saat",
    "Kap Saat",
    "Tarih",
    "Result",
    "Signal",
    "Signal Detail",
    "Pozisyon",
    "Trend (1s)",
    "Not",
]

# Per-column widths (centimetres), in the same order as HEADERS.
COLUMN_WIDTHS = ["1.6cm", "2.6cm", "1.4cm", "3.2cm", "3.2cm",
                 "2.4cm", "2.4cm", "2.9cm", "3.2cm", "1.6cm", "7cm", "2.2cm",
                 "2.2cm", "8cm"]

SHEET_NAME = "Sheet1"

# Style names used throughout the generated document.
S_HEADER = "hdrCell"
S_COIN_LONG = "coinLong"    # coin name when the position is Long (BUY)  -> green
S_COIN_SHORT = "coinShort"  # coin name when the position is Short (SELL) -> red
S_WIN = "resWin"
S_LOSS = "resLoss"
S_CELL = "dataCell"
S_NOTE = "noteCell"  # manual notes column (N): left-aligned free text


# ----------------------------------------------------------------------------
# VALUE FORMATTING
# ----------------------------------------------------------------------------
def _num_text(value):
    """Format a number for display without scientific notation.

    Picks the number of decimals from the magnitude (so small-cap coins such as
    SHIB read 0.00002315 instead of 2.315e-05) and trims trailing zeros.
    """
    if value is None:
        return ""
    magnitude = abs(value)
    if magnitude == 0:
        return "0"
    if magnitude >= 1:
        text = f"{value:.4f}"
    elif magnitude >= 0.01:
        text = f"{value:.6f}"
    else:
        text = f"{value:.8f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


# ----------------------------------------------------------------------------
# CELL BUILDERS
# ----------------------------------------------------------------------------
def _string_cell(text, stylename=S_CELL):
    cell = TableCell(valuetype="string", stylename=stylename)
    cell.addElement(P(text="" if text is None else str(text)))
    return cell


def _float_cell(number, display=None, stylename=S_CELL):
    cell = TableCell(valuetype="float", value=number, stylename=stylename)
    cell.addElement(P(text=display if display is not None else _num_text(number)))
    return cell


def _empty_cell(stylename=S_CELL):
    return TableCell(stylename=stylename)


def _passthrough_cell(cell):
    """Rebuild a manually-entered cell exactly as it was read.

    Keeps the cell's formula and numeric value when present, so a live formula
    (e.g. an =AVERAGE the user typed) survives a regeneration instead of being
    flattened to plain text.
    """
    attrs = {"stylename": S_NOTE}
    if cell.get("formula"):
        attrs["formula"] = cell["formula"]
    if cell.get("valuetype"):
        attrs["valuetype"] = cell["valuetype"]
    if cell.get("value") not in (None, ""):
        attrs["value"] = cell["value"]
    table_cell = TableCell(**attrs)
    table_cell.addElement(P(text=cell.get("text") or ""))
    return table_cell


# ----------------------------------------------------------------------------
# DOCUMENT STYLING
# ----------------------------------------------------------------------------
def _register_styles(doc):
    """Add the header, data, coin-direction and result cell styles.

    Every cell is centred (horizontally and vertically) with a thin border.
    """
    def cell_style(name, *, background=None, colour=None, bold=False, align="center"):
        style = Style(name=name, family="table-cell")
        cell_props = {"border": "0.5pt solid #d9d9d9", "verticalalign": "middle"}
        if background:
            cell_props["backgroundcolor"] = background
        style.addElement(TableCellProperties(**cell_props))
        if colour or bold:
            style.addElement(TextProperties(color=colour or "#000000",
                                            fontweight="bold" if bold else "normal"))
        style.addElement(ParagraphProperties(textalign=align))
        doc.automaticstyles.addElement(style)

    cell_style(S_HEADER, background="#1F3864", colour="#FFFFFF", bold=True)
    cell_style(S_CELL)
    cell_style(S_COIN_LONG, colour="#107C41", bold=True)   # Long  -> green
    cell_style(S_COIN_SHORT, colour="#C00000", bold=True)  # Short -> red
    cell_style(S_WIN, colour="#107C41", bold=True)         # WIN   -> green
    cell_style(S_LOSS, colour="#C00000", bold=True)        # LOSS  -> red
    cell_style(S_NOTE, align="left")                       # Not   -> left-aligned


def _add_columns(doc, table):
    """Give each column its own width."""
    for i, width in enumerate(COLUMN_WIDTHS):
        col_style = Style(name=f"co{i}", family="table-column")
        col_style.addElement(TableColumnProperties(columnwidth=width))
        doc.automaticstyles.addElement(col_style)
        table.addElement(TableColumn(stylename=f"co{i}"))


# ----------------------------------------------------------------------------
# READING AN EXISTING FILE BACK INTO PLAIN DICTS
# ----------------------------------------------------------------------------
def _cell_text(cell):
    return "".join(str(p) for p in cell.getElementsByType(P))


def _expanded_cells(row):
    """Return one cell object per logical column, expanding repeated cells."""
    out = []
    for cell in row.getElementsByType(TableCell):
        repeated = cell.getAttribute("numbercolumnsrepeated")
        count = min(int(repeated), 64) if repeated else 1
        out.extend([cell] * count)
    return out


def _row_has_user_content(cells, index):
    """True when a non-trade row still holds something the user typed by hand.

    A row that only carries a zero Trade number and a default Pozisyon
    ("Long"/"Short") — the leftovers an older writer could emit — counts as
    empty and is dropped. A formula anywhere, or any other non-empty cell (for
    example an average typed into the Not column on its own row), counts as
    real content worth keeping.
    """
    trade_i = index.get("Trade")
    pos_i = index.get("Pozisyon")
    for i, cell in enumerate(cells):
        if cell.getAttribute("formula"):
            return True
        text = _cell_text(cell).strip()
        if not text:
            continue
        if i == trade_i and text in ("0", "0.0"):
            continue
        if i == pos_i and text in ("Long", "Short"):
            continue
        return True
    return False


def _capture_cells(cells):
    """Snapshot a manual row up to its last non-empty cell, so it can be
    written back unchanged (text, formula and numeric value preserved)."""
    captured = []
    last = -1
    for i, cell in enumerate(cells):
        if i >= len(HEADERS):
            break
        text = _cell_text(cell)
        formula = cell.getAttribute("formula")
        captured.append({
            "text": text,
            "formula": formula,
            "valuetype": cell.getAttribute("valuetype"),
            "value": cell.getAttribute("value"),
        })
        if text.strip() or formula:
            last = i
    return captured[:last + 1]


def _read_records(path, include_manual=False):
    """Read an existing log file into a list of records (empty if none).

    Trade rows become trade dicts. When ``include_manual`` is set, rows the
    user added by hand (no trade data, but real content such as an average)
    are also returned as ``{"manual": True, "cells": [...]}`` so the writer can
    keep them; callers that only want trades (e.g. the stats report) leave it
    off and never see those rows.
    """
    if not os.path.exists(path):
        return []
    doc = load(path)
    tables = doc.spreadsheet.getElementsByType(Table)
    if not tables:
        return []
    rows = tables[0].getElementsByType(TableRow)
    if not rows:
        return []

    def as_float(cell):
        value = cell.getAttribute("value")
        if value not in (None, ""):
            try:
                return float(value)
            except ValueError:
                pass
        text = _cell_text(cell).strip()
        try:
            return float(text) if text else None
        except ValueError:
            return None

    # Map each header label to its column index, so the file is read by column
    # NAME rather than position. This makes reading robust to layout changes
    # (e.g. an older file that still has a Pozisyon column migrates cleanly).
    header_cells = _expanded_cells(rows[0])
    index = {_cell_text(c).strip(): i for i, c in enumerate(header_cells)}

    records = []
    for row in rows[1:]:
        cells = _expanded_cells(row)
        if len(cells) < 3:
            continue

        def text_of(name):
            i = index.get(name)
            return _cell_text(cells[i]).strip() if i is not None and i < len(cells) else ""

        def float_of(name):
            i = index.get(name)
            return as_float(cells[i]) if i is not None and i < len(cells) else None

        # Classify the row. A real trade has a coin name, an entry price, or a
        # positive trade number. Anything else is either a blank row left behind
        # by a spreadsheet editor (dropped) or a row the user added by hand —
        # e.g. an average/summary typed into the Not column on its own row —
        # which is kept verbatim so it is no longer wiped on the next write.
        trade_no = float_of("Trade") or 0
        is_trade = bool(text_of("Stock Name")) \
            or float_of("Başlangıç Fiyatı") is not None \
            or trade_no > 0
        if not is_trade:
            if include_manual and _row_has_user_content(cells, index):
                records.append({"manual": True, "cells": _capture_cells(cells)})
            continue

        # Direction: read the Pozisyon column if present, otherwise recover it
        # from the colour of the coin-name cell (older files).
        if "Pozisyon" in index:
            position = text_of("Pozisyon") or "Long"
        else:
            i = index.get("Stock Name")
            style = cells[i].getAttribute("stylename") if i is not None and i < len(cells) else None
            position = "Short" if style == S_COIN_SHORT else "Long"

        records.append({
            "trade": int(trade_no),
            "base": text_of("Stock Name"),
            "quote": text_of("To"),
            "entry": float_of("Başlangıç Fiyatı"),
            "close": float_of("Kapanış Fiyatı"),
            "open_time": text_of("Açılış Saat"),
            "close_time": text_of("Kap Saat"),
            "position": position,
            "date": text_of("Tarih"),
            "result": text_of("Result"),
            "signal": float_of("Signal"),
            "signals": text_of("Signal Detail"),
            "htf_trend": text_of("Trend (1s)"),
            # Manual notes column (N): read it back so anything typed by hand is
            # preserved when the file is regenerated on the next write.
            "note": text_of("Not"),
        })
    return records


# ----------------------------------------------------------------------------
# WRITING THE WHOLE FILE
# ----------------------------------------------------------------------------
def _atomic_save(doc, path):
    """Save to a temp file in the same directory, then move it into place.

    os.replace is atomic on POSIX, so a reader (or a crash mid-save) never sees
    a half-written .ods and the previous file survives until the new one is
    complete — important when the analyzer rewrites the log while the user has
    it open in a spreadsheet.
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".trade_log_", suffix=".ods", dir=directory)
    os.close(fd)
    try:
        doc.save(tmp)
        # mkstemp creates the temp file as 0600; keep the existing file's
        # permissions (or a sane default for a brand-new file) across the
        # replace, so a save never silently tightens access to the log.
        try:
            os.chmod(tmp, os.stat(path).st_mode)
        except FileNotFoundError:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


@contextmanager
def _file_lock(path):
    """Serialise read-modify-write across processes via a sidecar lock file.

    Guards against the analyzer being run from more than one session at once,
    where two processes could otherwise overwrite each other's rows. A no-op
    where fcntl is unavailable. (It cannot coordinate with a spreadsheet app
    holding the file open — that race is mitigated by re-reading right before
    every write, not by this lock.)
    """
    if fcntl is None:
        yield
        return
    handle = open(path + ".lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def _write_records(path, records):
    """Regenerate the styled .ods file from a list of trade/manual records."""
    doc = OpenDocumentSpreadsheet()
    _register_styles(doc)
    table = Table(name=SHEET_NAME)
    _add_columns(doc, table)

    header = TableRow()
    for title in HEADERS:
        header.addElement(_string_cell(title, stylename=S_HEADER))
    table.addElement(header)

    for record in records:
        # Manual rows the user added (e.g. an average) are written back exactly
        # as they were read, including any formula, and never treated as trades.
        if record.get("manual"):
            row = TableRow()
            for cell in record["cells"]:
                row.addElement(_passthrough_cell(cell))
            table.addElement(row)
            continue

        # Direction is conveyed by the colour of the coin name (no text column).
        coin_style = S_COIN_SHORT if record["position"] == "Short" else S_COIN_LONG
        result = record["result"]
        res_style = S_CELL
        if result.startswith("WIN"):
            res_style = S_WIN
        elif result.startswith("LOSS"):
            res_style = S_LOSS

        row = TableRow()
        row.addElement(_float_cell(record["trade"], display=str(record["trade"])))
        row.addElement(_string_cell(record["base"], stylename=coin_style))
        row.addElement(_string_cell(record["quote"]))
        row.addElement(_float_cell(record["entry"]) if record["entry"] is not None
                       else _empty_cell())
        row.addElement(_float_cell(record["close"]) if record["close"] is not None
                       else _empty_cell())
        row.addElement(_string_cell(record["open_time"]))
        row.addElement(_string_cell(record["close_time"]))
        row.addElement(_string_cell(record["date"]))
        row.addElement(_string_cell(result, stylename=res_style))
        row.addElement(_float_cell(record["signal"], display=str(int(record["signal"])))
                       if record["signal"] is not None else _empty_cell())
        row.addElement(_string_cell(record.get("signals", "")))
        # Pozisyon column: Long in green, Short in red, matching the coin-name
        # colour so direction is readable as text too.
        row.addElement(_string_cell(record["position"], stylename=coin_style))
        # Trend (1s) column: the aligned 1-hour trend (UP/DOWN/NEUTRAL).
        row.addElement(_string_cell(record.get("htf_trend", "")))
        # Not column (N, last): the user's manual note, kept verbatim.
        row.addElement(_string_cell(record.get("note", ""), stylename=S_NOTE))
        table.addElement(row)

    doc.spreadsheet.addElement(table)
    _atomic_save(doc, path)


# ----------------------------------------------------------------------------
# THE LOGGER
# ----------------------------------------------------------------------------
class TradeLogger:
    """Appends displayed signals to an .ods file and fills in their outcomes."""

    def __init__(self, path, hold_minutes):
        self.path = path
        self._hold_minutes = hold_minutes
        # symbol -> datetime it was last logged, to suppress duplicate rows on
        # consecutive refreshes within the same hold window.
        self._last_logged = {}

    # -- timing helpers ------------------------------------------------------
    def _close_dt(self, record, reference):
        """Build a datetime for a record's Kap Saat from its date + close time.

        Kap Saat is stored as HH:MM against a single trade date, but the hold can
        cross midnight: the live max-hold cap is 24h (so the close HH:MM equals the
        open HH:MM on the NEXT day), and even a short hold opened late at night can
        roll over. So when the close time is at or before the open time, the close
        falls on a later day — advance whole days until it is past the open. This
        keeps the auto-close firing at the real exit time instead of ~now.
        (Assumes the hold is under 24h OR exactly the 24h cap, which our config is.)
        """
        try:
            day = datetime.strptime(record["date"], "%d/%m/%Y").date()
        except (ValueError, KeyError):
            day = reference.date()
        try:
            hour, minute = (int(x) for x in record["close_time"].split(":"))
        except (ValueError, AttributeError):
            return None
        midnight = datetime.combine(day, datetime.min.time())
        close_dt = midnight.replace(hour=hour, minute=minute)
        # Roll the close to a later day if it is not strictly after the open time.
        try:
            oh, om = (int(x) for x in (record.get("open_time") or "").split(":"))
            open_dt = midnight.replace(hour=oh, minute=om)
            while close_dt <= open_dt:
                close_dt += timedelta(days=1)
        except (ValueError, AttributeError):
            pass
        return close_dt

    # -- appending new signals ----------------------------------------------
    def _should_log(self, result, now):
        last = self._last_logged.get(result["symbol"])
        if last is None:
            return True
        hold = self._hold_minutes[result["strength"]]
        return (now - last).total_seconds() >= hold * 60

    def _new_record(self, result, now, trade_number):
        base, _, quote = result["symbol"].partition("/")
        strength = result["strength"]
        hold = self._hold_minutes[strength]
        exit_time = now + timedelta(minutes=hold)
        # The measured value behind each agreeing indicator, in display order,
        # e.g. "RSI 24 | MACD 0.12% | BB 0.30% | VOL 3.2x".
        details = result.get("details", {})
        signals = " | ".join(
            details.get(name) or name for name in result.get("contributors", [])
        )
        return {
            "trade": trade_number,
            "base": base,
            "quote": quote,
            "entry": result["price"],
            "close": None,
            "open_time": f"{now:%H:%M}",
            "close_time": f"{exit_time:%H:%M}",
            "position": "Long" if result["direction"] == "BUY" else "Short",
            "date": f"{now:%d/%m/%Y}",
            "result": "",
            "signal": float(result["score"]),
            "signals": signals,
            "htf_trend": result.get("htf_trend", ""),
            "note": "",  # left blank for the user to fill in by hand
        }

    def log(self, results, now):
        """Append a row for each fresh signal. Returns the number of rows added."""
        with _file_lock(self.path):
            # include_manual=True so the user's hand-added rows are read in and
            # written straight back out, instead of being dropped on this save.
            records = _read_records(self.path, include_manual=True)

            # A coin with a still-open position on the board is not logged again,
            # even across restarts (also covers in-memory dedup after a crash).
            open_positions = set()
            for record in records:
                if record.get("manual"):
                    continue
                if record["close"] is None:
                    close_dt = self._close_dt(record, now)
                    if close_dt is None or close_dt > now:
                        open_positions.add((record["base"], record["position"]))

            fresh = []
            for result in results:
                base = result["symbol"].split("/")[0]
                position = "Long" if result["direction"] == "BUY" else "Short"
                if (base, position) in open_positions:
                    continue
                if not self._should_log(result, now):
                    continue
                fresh.append(result)

            if not fresh:
                return 0

            next_number = max((r["trade"] for r in records if not r.get("manual")),
                              default=0) + 1
            new_records = []
            for result in fresh:
                new_records.append(self._new_record(result, now, next_number))
                next_number += 1
                self._last_logged[result["symbol"]] = now

            # Insert new trades right after the last real trade row, so any
            # manual summary rows the user keeps at the bottom stay at the
            # bottom (and the new trades land above them, in trade order).
            insert_at = len(records)
            for i in range(len(records) - 1, -1, -1):
                if not records[i].get("manual"):
                    insert_at = i + 1
                    break
            records[insert_at:insert_at] = new_records

            _write_records(self.path, records)
            return len(fresh)

    # -- filling in outcomes -------------------------------------------------
    def _price_at(self, exchange, symbol, close_dt):
        """Return the market price at (or just after) close_dt, or None."""
        since = int(close_dt.timestamp() * 1000)
        candles = exchange.fetch_ohlcv(symbol, timeframe="1m", since=since, limit=1)
        if not candles:
            return None
        # Use the candle's open: the price at the start of the exit minute.
        return float(candles[0][1])

    def update_closed_trades(self, exchange, now):
        """Fill Kapanış Fiyatı + Result for trades whose Kap Saat has passed.

        Only blank cells are touched, so anything filled in by hand is kept.
        Returns the number of trades closed on this pass.
        """
        with _file_lock(self.path):
            # include_manual=True so the user's hand-added rows are preserved
            # when this pass rewrites the file.
            records = _read_records(self.path, include_manual=True)
            if not records:
                return 0

            closed = 0
            for record in records:
                if record.get("manual"):
                    continue
                if record["close"] is not None or record["entry"] is None:
                    continue
                close_dt = self._close_dt(record, now)
                if close_dt is None or close_dt > now:
                    continue  # the suggested exit has not arrived yet
                symbol = f"{record['base']}/{record['quote']}"
                try:
                    price = self._price_at(exchange, symbol, close_dt)
                except Exception:
                    price = None
                if price is None:
                    continue

                entry = record["entry"]
                if record["position"] == "Long":
                    profit_pct = (price - entry) / entry * 100 if entry else 0.0
                else:
                    profit_pct = (entry - price) / entry * 100 if entry else 0.0
                record["close"] = price
                record["result"] = f"{'WIN' if profit_pct >= 0 else 'LOSS'} {profit_pct:+.2f}%"
                closed += 1

            if closed:
                _write_records(self.path, records)
            return closed
