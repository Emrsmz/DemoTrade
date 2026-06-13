#!/usr/bin/env python3
"""
================================================================================
 TELEGRAM NOTIFIER  -  PUSHES SIGNALS AND ERRORS TO A TELEGRAM CHAT
================================================================================

This module sends the analyzer's BUY/SELL signals and any critical errors to a
Telegram chat, so trades can be acted on without watching the terminal.

WHAT IT SENDS
-------------
  * BUY (AL) signal   - coin, price, and the RSI value (plus the other agreeing
                        indicators), formatted in readable Turkish with emoji.
  * SELL (SAT) signal  - the same, for a sell recommendation.
  * Errors             - connection failures or any critical exception, so a
                        silent crash is noticed.

INTERACTIVE COMMAND (/tara)
---------------------------
`run_command_bot()` starts a long-polling bot so a scan can be triggered on
demand: send /tara in the chat and the bot runs one scan and replies with the
signals. Only the configured chat id may use it. The analyzer exposes this via
`python signal_analyzer.py --bot`.

CONFIGURATION (NEVER HARD-CODED)
--------------------------------
The bot token and chat id are read from environment variables, loaded from a
local .env file if python-dotenv is installed:

    TELEGRAM_BOT_TOKEN   - the token from @BotFather
    TELEGRAM_CHAT_ID     - the target chat/channel id

If either variable is missing the notifier silently disables itself, so the
analyzer keeps running normally without Telegram. API keys are NEVER written in
code; they always come from the environment.

DESIGN NOTES
------------
  * python-telegram-bot (v20+) is fully asynchronous. The analyzer is
    synchronous, so a single private event loop is kept on the instance and used
    to run each send to completion. This avoids creating/closing a loop per call.
  * The board refreshes every 60 seconds and would otherwise re-send the same
    standing signals each cycle. A small de-duplication cache keyed by
    (symbol, direction, trigger time) ensures each distinct signal is sent once.
  * Every send is wrapped in try/except: a Telegram outage must never stop the
    market scan.

DISCLAIMER: This tool is for educational and informational purposes only. It is
not financial advice.
================================================================================
"""

import asyncio
import html
import os

try:
    # Optional: load TELEGRAM_* variables from a local .env file if present.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    # python-dotenv is optional; real environment variables still work.
    pass

try:
    from telegram import Bot
    from telegram.constants import ParseMode

    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False


# Environment variable names. The values themselves live only in the
# environment / .env file, never in this source file.
TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
CHAT_ID_ENV = "TELEGRAM_CHAT_ID"

# Emoji per direction, used in the message headline.
DIRECTION_EMOJI = {"BUY": "\U0001F7E2", "SELL": "\U0001F534"}  # green / red circle
# Turkish label per direction shown to the user.
DIRECTION_LABEL = {"BUY": "AL", "SELL": "SAT"}


def format_price(price):
    """Format a price with a sensible number of decimals for its magnitude.

    Mirrors the analyzer's own price formatting so messages match the board.
    """
    if price >= 1:
        return f"${price:,.2f}"
    if price >= 0.01:
        return f"${price:,.4f}"
    return f"${price:,.8f}"


class TelegramNotifier:
    """Sends BUY/SELL signals and errors to a configured Telegram chat.

    Create one instance and reuse it. If the library or the credentials are
    missing the instance is `enabled == False` and every send becomes a no-op,
    so callers never need to guard their calls.
    """

    def __init__(self, token=None, chat_id=None):
        # Credentials come from the environment by default; explicit arguments
        # are mainly for testing. Never hard-code real tokens here.
        self.token = token or os.environ.get(TOKEN_ENV)
        self.chat_id = chat_id or os.environ.get(CHAT_ID_ENV)

        self._bot = None
        self._loop = None
        # Remembers signals already sent so a standing signal is not re-sent on
        # every 60-second refresh. Keys are (symbol, direction, trigger_time).
        self._sent_keys = set()

        if not _TELEGRAM_AVAILABLE:
            self.enabled = False
            self.reason = (
                "python-telegram-bot is not installed "
                "(pip install python-telegram-bot)"
            )
            return
        if not self.token or not self.chat_id:
            self.enabled = False
            self.reason = (
                f"set {TOKEN_ENV} and {CHAT_ID_ENV} in the environment or .env"
            )
            return

        try:
            self._bot = Bot(token=self.token)
            self._loop = asyncio.new_event_loop()
            self.enabled = True
            self.reason = "ready"
        except Exception as error:  # malformed token, etc.
            self.enabled = False
            self.reason = f"could not initialize bot: {error}"

    # -- low-level send ------------------------------------------------------
    def _send(self, text):
        """Send one HTML message. Returns True on success, False on any failure.

        Failures are swallowed (and reported via the return value) so a Telegram
        problem can never interrupt the market scan.
        """
        if not self.enabled:
            return False
        try:
            self._loop.run_until_complete(
                self._bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            )
            return True
        except Exception:
            return False

    # -- public API ----------------------------------------------------------
    def notify_signal(self, result):
        """Send a BUY (AL) or SELL (SAT) message for one analyzer result dict.

        `result` is the dict produced by analyze_symbol(): it carries `symbol`,
        `direction`, `price`, `score`, `details` (e.g. {"RSI": "RSI 24", ...}),
        and `trigger_time`. The same standing signal is only sent once.

        Returns True if a message was sent, False if it was skipped (disabled,
        already sent, or a send failure).
        """
        if not self.enabled:
            return False

        symbol = result["symbol"]
        direction = result["direction"]
        trigger = result.get("trigger_time")

        # De-duplicate: skip a signal already announced for this exact trigger.
        key = (symbol, direction, trigger.isoformat() if trigger else None)
        if key in self._sent_keys:
            return False

        if self._send(self._format_signal(result)):
            self._sent_keys.add(key)
            return True
        return False

    def notify_signals(self, results):
        """Send a message for each new signal in a list. Returns the count sent."""
        return sum(1 for result in results if self.notify_signal(result))

    def notify_error(self, context, error=None):
        """Send a critical-error alert (e.g. connection failure).

        `context` is a short human description of where it happened; `error` is
        the optional exception/detail. Errors are not de-duplicated.
        """
        if not self.enabled:
            return False
        detail = f"\n<code>{html.escape(str(error))}</code>" if error else ""
        text = (
            f"⚠️ <b>HATA</b>\n"  # warning sign
            f"\U0001F539 <b>Nerede:</b> {html.escape(str(context))}"
            f"{detail}\n\n"
            f"<i>Bot calismaya devam ediyor, lutfen kontrol edin.</i>"
        )
        return self._send(text)

    def notify_startup(self):
        """Send a one-off 'analyzer started' message. Best-effort, never required."""
        if not self.enabled:
            return False
        return self._send("\U0001F680 <b>Kripto Sinyal Analizoru baslatildi</b>")

    # -- interactive command bot (/tara) -------------------------------------
    def _is_owner(self, update):
        """True only for the configured chat, so the bot ignores other users."""
        chat = update.effective_chat
        return chat is not None and str(chat.id) == str(self.chat_id)

    def run_command_bot(self, scan_callback, coin_count=0, stats_callback=None):
        """Run an interactive bot that scans on demand via the /tara command.

        `scan_callback` is a no-argument callable returning (results, unavailable),
        where `results` is the list of analyzer result dicts. It is run in a
        worker thread so the network-heavy scan never blocks the bot's event
        loop. `stats_callback`, if given, is a no-argument callable returning an
        HTML performance report string, exposed as the /istatistik command. This
        call BLOCKS (long-polling) until interrupted (Ctrl+C).

        Only the configured TELEGRAM_CHAT_ID may use the commands; messages from
        anyone else are ignored.
        """
        if not self.enabled:
            print(f"Cannot start command bot: {self.reason}")
            return False

        # Imported here so the module still loads when the library is absent.
        from telegram import BotCommand
        from telegram.ext import ApplicationBuilder, CommandHandler

        # The full command catalogue (command, Turkish description), built once so
        # /start, /help and /komutlarim all stay in sync. /istatistik is only
        # listed when a stats callback is wired in.
        catalogue = [("/tara", "Anlik tarama yapar, AL/SAT sinyallerini gosterir")]
        if stats_callback is not None:
            catalogue.append(
                ("/istatistik", "Performans raporu (basari orani, K/Z, coin bazli)")
            )
        catalogue.append(("/komutlarim", "Kullanabilecegin tum komutlari listeler"))
        catalogue.append(("/start", "Botu baslatir ve kisa bilgi verir"))

        command_list_text = "\U0001F4CB <b>Komutlar</b>\n" + "\n".join(
            f"<b>{cmd}</b> — {desc}" for cmd, desc in catalogue
        )

        async def cmd_start(update, context):
            if not self._is_owner(update):
                return
            await update.message.reply_text(
                "\U0001F44B <b>Kripto Sinyal Botu hazir.</b>\n\n" + command_list_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

        async def cmd_komutlarim(update, context):
            if not self._is_owner(update):
                return
            await update.message.reply_text(
                command_list_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

        async def cmd_istatistik(update, context):
            if not self._is_owner(update):
                return
            loop = asyncio.get_running_loop()
            try:
                report = await loop.run_in_executor(None, stats_callback)
            except Exception as error:
                await update.message.reply_text(f"⚠️ Rapor hatasi: {error}")
                return
            await update.message.reply_text(
                report, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )

        async def cmd_tara(update, context):
            if not self._is_owner(update):
                return
            await update.message.reply_text("\U0001F50D Taraniyor, lutfen bekleyin...")
            # Run the blocking scan off the event loop so polling stays responsive.
            loop = asyncio.get_running_loop()
            try:
                results, _ = await loop.run_in_executor(None, scan_callback)
            except Exception as error:
                await update.message.reply_text(f"⚠️ Tarama hatasi: {error}")
                return
            if not results:
                await update.message.reply_text(
                    f"\U0001F4ED {coin_count} coin tarandi, su an aktif sinyal yok."
                )
                return
            await update.message.reply_text(
                f"✅ {coin_count} coin tarandi, {len(results)} sinyal bulundu:"
            )
            # On-demand results are always shown in full (no de-duplication).
            for result in results:
                await update.message.reply_text(
                    self._format_signal(result),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )

        async def _post_init(application):
            # Register the commands so Telegram shows them in the "/" menu.
            await application.bot.set_my_commands(
                [BotCommand(cmd.lstrip("/"), desc) for cmd, desc in catalogue]
            )

        app = ApplicationBuilder().token(self.token).post_init(_post_init).build()
        app.add_handler(CommandHandler(["start", "help"], cmd_start))
        app.add_handler(CommandHandler("komutlarim", cmd_komutlarim))
        app.add_handler(CommandHandler("tara", cmd_tara))
        if stats_callback is not None:
            app.add_handler(CommandHandler("istatistik", cmd_istatistik))

        # Announce readiness before blocking on long-polling.
        self._send(
            "\U0001F680 <b>Komut botu aktif</b>\n"
            "Komutlari gormek icin <b>/komutlarim</b> yazin." + "\n\n"
            + command_list_text
        )
        app.run_polling()
        return True

    # -- message formatting --------------------------------------------------
    def _format_signal(self, result):
        """Build the readable Turkish HTML message for one signal."""
        direction = result["direction"]
        emoji = DIRECTION_EMOJI.get(direction, "")
        label = DIRECTION_LABEL.get(direction, direction)
        symbol = html.escape(result["symbol"])
        price_str = format_price(result["price"])
        score = result.get("score", 0)
        available = result.get("available", 6)

        details = result.get("details", {}) or {}
        # The RSI value is the headline indicator the user asked to see; it is
        # present as e.g. "RSI 24" when RSI is one of the agreeing signals.
        rsi_text = details.get("RSI") or "Yok"

        # The remaining agreeing indicators, shown so the full picture is visible.
        other = [
            value
            for name, value in details.items()
            if name != "RSI" and value
        ]
        other_line = (
            f"\n\U0001F4CA <b>Diger sinyaller:</b> {html.escape(' | '.join(other))}"
            if other
            else ""
        )

        # The ADX market regime that this signal was judged under, plus which DI
        # line confirmed the direction when the market is trending.
        regime = result.get("regime")
        adx = result.get("adx")
        plus_di, minus_di = result.get("plus_di"), result.get("minus_di")
        di_text = ""
        if regime == "TREND" and plus_di is not None and minus_di is not None:
            di_text = ", +DI>−DI" if plus_di >= minus_di else ", −DI>+DI"
        regime_line = (
            f"\n\U0001F9ED <b>Rejim:</b> {html.escape(str(regime))}"
            f" (ADX {adx:.0f}{di_text})"
            if regime and adx is not None
            else ""
        )

        # The aligned higher-timeframe (1h) trend.
        htf_trend = result.get("htf_trend")
        htf_arrow = {"UP": "↑", "DOWN": "↓"}.get(htf_trend, "→")
        htf_line = (
            f"\n\U0001F551 <b>1s trend:</b> {htf_arrow} {html.escape(str(htf_trend))}"
            if htf_trend
            else ""
        )

        trigger = result.get("trigger_time")
        trigger_line = (
            f"\n⏰ <b>Sinyal zamani:</b> {trigger:%H:%M:%S}" if trigger else ""
        )

        return (
            f"{emoji} <b>{label} SINYALI</b> {emoji}\n"
            f"\U0001FA99 <b>Coin:</b> {symbol}\n"
            f"\U0001F4B0 <b>Fiyat:</b> {price_str}\n"
            f"\U0001F4C8 <b>RSI:</b> {html.escape(str(rsi_text))}\n"
            f"✅ <b>Guc:</b> {score}/{available} sinyal"
            f"{other_line}"
            f"{regime_line}"
            f"{htf_line}"
            f"{trigger_line}"
        )

    def close(self):
        """Close the private event loop. Safe to call even when disabled."""
        if self._loop is not None:
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None


# A quick manual self-test: `python telegram_notifier.py` sends one test message
# using the credentials in the environment / .env, so the setup can be verified
# before wiring it into the analyzer.
if __name__ == "__main__":
    from datetime import datetime

    notifier = TelegramNotifier()
    if not notifier.enabled:
        print(f"Telegram notifier disabled: {notifier.reason}")
        raise SystemExit(1)

    sample = {
        "symbol": "BTC/USDT",
        "direction": "BUY",
        "price": 64231.55,
        "score": 4,
        "details": {"RSI": "RSI 27", "MACD": "MACD 0.12%", "VOL": "VOL 3.2x"},
        "trigger_time": datetime.now(),
    }
    ok = notifier.notify_signal(sample)
    print("Test message sent." if ok else "Failed to send test message.")
    notifier.close()
