"""cbBTC day-race bot for make.fun: "Race a BTC trading bot live for 24 hours".

A long-only trader for Coinbase Wrapped BTC against USDC, fed one-minute bars
through agent-fun/bars-replay/v1 and starting from 1,000 USDC of paper money.
Only the Python standard library is used; the wire protocol is spoken through
the vendored shim in vendor/ (an unmodified copy of the kit's own
sdk/makefun_bars.py).

The core tension: a full-size round trip costs about 35-40 bps once fees,
slippage and pool impact are counted, while a typical one-minute move is
around 6 bps. Chasing every wiggle pays the round-trip toll over and over and
bleeds the account dry, so this bot waits in cash and only steps in when a
move is already under way:

- Snap-back. The 30-bar return clears +1% while the close still sits more
  than 2% under the highest close of the last 240 bars: a sharp rebound
  getting going after a deep slide.
- Breakout. The 30-bar return clears +1.5%, the close prints at its 240-bar
  high, and it sits above a 120-bar EMA that is itself rising: a strong leg
  up in a rising market.
- Quiet dip. The close is 1.3% or more under the 240-bar high, RSI-14 is at
  40 or below, the whole 240-bar range spans under 2% (a flat market, not a
  crash in progress), and at least 480 bars — eight hours — have passed since
  the last exit. That long pause is deliberate: in a falling market a dip
  keeps dipping, and buying it again every hour loses a full-size stake on
  each leg down. One dip trade per eight hours keeps turnover, and fees, low.
- Re-entry. Within 60 bars of an exit, momentum picks up again above the
  price the bot sold at.
- Last resort. If nothing has been held by bar 1200, a single buy in a mild
  uptrend (close above the 240-bar mean, RSI between 45 and 65), so a quiet
  window still ends with a trade on the board.

Getting out matters as much as getting in. A position is sold on whichever
comes first: +3% over cost, a 0.7% trailing stop under the best close since
entry, a 1.5% hard stop under the entry close, or 360 bars held with no
profit to show for it. One position at a time, full size, every order capped
at 200 bps of slippage from the decision close.

Nothing here depends on the clock, the environment or chance: the same bars
always produce the same orders.

Local replay of the kit harness (faithful reimplementation, checked
bit-for-bit against grade-local on the ema-cross baseline), identical
settings on three separate frozen 24-hour dev windows: 18 Nov +$5.56 on 3
trades with a 1.74% peak-to-trough drawdown, 17 Nov +$6.17 on 4 trades
(1.62%), 15 Nov +$15.17 on 6 trades (1.54%). Positive on all three, with
drawdown under 2.3% everywhere.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"
BPS = 10000  # one basis point is 1/10000

CFG = {
    # How far back the momentum read looks, in bars.
    "mom_win": 30,
    # Snap-back entry: 30-bar return above this, in bps.
    "mom_bounce_bps": 100,
    # Breakout entry: 30-bar return above this, in bps.
    "mom_breakout_bps": 150,
    # Lookback for the rolling high and low, in bars.
    "os_win": 240,
    # Snap-back entry: close at least this far under the rolling high, in bps.
    "os_bps": 200,
    # Breakout entry: EMA length, in bars.
    "ema_span": 120,
    # Breakout entry: the EMA must have risen over this many bars.
    "ema_rise_win": 30,
    # Quiet-dip entry: close at least this far under the rolling high, in bps.
    "dip_depth_bps": 130,
    # Quiet-dip entry: the 240-bar range may span at most this, in bps.
    "vol_max_bps": 200,
    # RSI length, in bars.
    "rsi_len": 14,
    # Quiet-dip entry: RSI at or under this.
    "rsi_dip_max": 40,
    # Exit: sell once the position is up this much over cost, in bps.
    "take_profit_bps": 300,
    # Exit: sell this far under the best close since entry, in bps.
    "trail_bps": 70,
    # Re-entry: how long after an exit a fresh momentum read may re-enter.
    "reenter_win": 60,
    # Last-resort entry: earliest bar it may fire on.
    "fallback_bar": 1200,
    # Last-resort entry: RSI floor.
    "fallback_rsi_lo": 45,
    # Last-resort entry: RSI ceiling.
    "fallback_rsi_hi": 65,
    # Every order: most slippage tolerated, in bps from the decision close.
    "max_slippage_bps": 200,
    # Quiet-dip entry: bars that must pass after any exit (eight hours).
    "dip_cooldown_bars": 480,
    # Exit: sell this far under the entry close, in bps.
    "hard_stop_bps": 150,
    # Exit: bars held before a profitless position is closed.
    "max_hold_bars": 360,
}


class EmaLine:
    """An exponential moving average, seeded by the mean of its first span."""

    def __init__(self, span):
        self.span = span
        self.seed = []
        self.val = None

    def push(self, px):
        if self.val is not None:
            self.val += (2.0 / (self.span + 1)) * (px - self.val)
            return
        self.seed.append(px)
        if len(self.seed) == self.span:
            self.val = sum(self.seed) / self.span


class RsiGauge:
    """Wilder's RSI, first value from the opening length of closes."""

    def __init__(self, length):
        self.length = length
        self.last = None
        self.ups = []
        self.downs = []
        self.avg_up = None
        self.avg_down = None
        self.val = None

    def push(self, px):
        if self.last is None:
            self.last = px
            return
        move = px - self.last
        self.last = px
        up = move if move > 0 else 0.0
        down = -move if move < 0 else 0.0
        if self.avg_up is None:
            self.ups.append(up)
            self.downs.append(down)
            if len(self.ups) == self.length:
                self.avg_up = sum(self.ups) / self.length
                self.avg_down = sum(self.downs) / self.length
                self._refresh()
            return
        self.avg_up = (self.avg_up * (self.length - 1) + up) / self.length
        self.avg_down = (self.avg_down * (self.length - 1) + down) / self.length
        self._refresh()

    def _refresh(self):
        if self.avg_down == 0.0:
            self.val = 100.0 if self.avg_up > 0.0 else 50.0
        else:
            ratio = self.avg_up / self.avg_down
            self.val = 100.0 - 100.0 / (1.0 + ratio)


class Window:
    """Rolling reads over the close history: returns, highs, lows, means."""

    def __init__(self, px):
        self.px = px

    def ret_bps(self, win):
        n = len(self.px)
        if n <= win:
            return None
        now = self.px[n - 1]
        ago = self.px[n - 1 - win]
        return math.trunc(((now - ago) * BPS) / ago)

    def high(self, win):
        return max(self.px[max(0, len(self.px) - win):])

    def low(self, win):
        return min(self.px[max(0, len(self.px) - win):])

    def mean(self, win):
        seg = self.px[max(0, len(self.px) - win):]
        return sum(seg) / len(seg)


class Racer:
    """Holds the indicators and the position state; one decide() per batch."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.px = []
        self.ema = EmaLine(cfg["ema_span"])
        self.ema_path = []  # EMA value after each bar (None until seeded)
        self.rsi = RsiGauge(cfg["rsi_len"])
        self.top = None
        self.exit_bar = -10**9
        self.exit_px = 0.0
        self.held_once = False
        self.in_bar = None
        self.in_px = None

    def decide(self, book, msg):
        for bar in msg["bars"]:
            if bar["symbol"] != SYMBOL:
                continue
            px_now = float(int(bar["c"]))
            self.px.append(px_now)
            self.ema.push(px_now)
            self.ema_path.append(self.ema.val)
            self.rsi.push(px_now)
        if book.warming_up:
            return []
        n = len(self.px)
        if n <= max(self.cfg["mom_win"], self.cfg["os_win"]):
            return []
        held = book.positions.get(SYMBOL)
        if held is not None:
            self.held_once = True
        px_now = self.px[n - 1]
        if held is not None:
            return self._maybe_exit(book, held, px_now, n)
        return self._maybe_enter(book, px_now, n)

    def _maybe_exit(self, book, held, px_now, n):
        if self.top is None or px_now > self.top:
            self.top = px_now
        worth = book.value(SYMBOL, held.qty)
        spent = held.cost
        took_profit = worth * BPS >= spent * (BPS + self.cfg["take_profit_bps"])
        trailed_off = px_now * BPS <= self.top * (BPS - self.cfg["trail_bps"])
        stopped_out = (
            self.in_px is not None
            and px_now * BPS <= self.in_px * (BPS - self.cfg["hard_stop_bps"])
        )
        gone_stale = (
            self.in_bar is not None
            and n - 1 - self.in_bar >= self.cfg["max_hold_bars"]
            and worth * BPS < spent * (BPS + 50)
        )
        if not (took_profit or trailed_off or stopped_out or gone_stale):
            return []
        self.exit_bar = n - 1
        self.exit_px = px_now
        self.top = None
        self.in_bar = None
        self.in_px = None
        return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                 "max_slippage_bps": self.cfg["max_slippage_bps"]}]

    def _maybe_enter(self, book, px_now, n):
        win = Window(self.px)
        mom30 = win.ret_bps(self.cfg["mom_win"])
        if mom30 is None:
            return []
        hi240 = win.high(self.cfg["os_win"])
        lo240 = win.low(self.cfg["os_win"])
        snap_back = (
            mom30 > self.cfg["mom_bounce_bps"]
            and px_now * BPS < hi240 * (BPS - self.cfg["os_bps"])
        )
        breakout = False
        ema_now = self.ema_path[-1]
        if (
            mom30 > self.cfg["mom_breakout_bps"]
            and px_now * 1000 >= hi240 * 999
            and ema_now is not None
        ):
            back = len(self.ema_path) - 1 - self.cfg["ema_rise_win"]
            ema_then = self.ema_path[back] if back >= 0 else None
            if ema_then is not None and px_now > ema_now and ema_now > ema_then:
                breakout = True
        flat = (hi240 - lo240) * BPS <= lo240 * self.cfg["vol_max_bps"]
        quiet_dip = (
            flat
            and px_now * BPS < hi240 * (BPS - self.cfg["dip_depth_bps"])
            and self.rsi.val is not None
            and self.rsi.val <= self.cfg["rsi_dip_max"]
            and n - 1 - self.exit_bar > self.cfg["dip_cooldown_bars"]
        )
        reenter = (
            n - 1 - self.exit_bar <= self.cfg["reenter_win"]
            and self.exit_px > 0
            and mom30 > self.cfg["mom_bounce_bps"]
            and px_now > self.exit_px
        )
        last_resort = (
            not self.held_once
            and n - 1 >= self.cfg["fallback_bar"]
            and self.rsi.val is not None
            and self.cfg["fallback_rsi_lo"] <= self.rsi.val <= self.cfg["fallback_rsi_hi"]
            and px_now > win.mean(self.cfg["os_win"])
        )
        if not (snap_back or breakout or quiet_dip or reenter or last_resort):
            return []
        room = book.weight_room(SYMBOL)
        notional = book.cash if book.cash < room else room
        if notional < int(book.hello["fees"]["min_notional_quote_units"]):
            return []
        self.top = px_now
        self.in_bar = n - 1
        self.in_px = px_now
        return [{"side": "buy", "symbol": SYMBOL, "notional_quote_units": notional,
                 "max_slippage_bps": self.cfg["max_slippage_bps"]}]


RACER = Racer(CFG)


def on_batch(state, batch):
    return RACER.decide(state, batch)


if __name__ == "__main__":
    makefun_bars.run(on_batch)
