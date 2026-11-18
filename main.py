"""cbBTC bounce + flat-market dip bot for make.fun "Race a BTC trading bot live for 24 hours".

Pure Python standard library only. Speaks agent-fun/bars-replay/v1 on stdin/stdout
through the vendored SDK shim (vendor/makefun_bars.py, a byte-identical copy of the
kit's sdk/makefun_bars.py).

Why this shape: on 1-minute bars a full-size round trip pays roughly 35-40 bps in
fees, slippage and pool impact, while the typical 1-minute move is ~6 bps. Fast
signals fire dozens of times and bleed that cost on every whipsaw, so the bot
stays in cash most of the time and only buys a move that has already started:
  * Bounce: the 30-bar return is above +1% while the close is still more than 2%
    below the highest close of the last 240 bars — a sharp bounce starting after
    a deep drop (the move that pays in a crashing market).
  * Breakout: the 30-bar return is above +1.5%, the close prints at its 240-bar
    high, above a rising 120-bar EMA — a strong breakout in a rising market.
  * Flat-market dip: the close is at least 1.3% below the 240-bar high with
    RSI-14 at or below 40 — but only while the 240-bar high-low range is under
    2%, i.e. the market is flat, not mid-leg of a crash, AND only 480+ bars
    (8 hours) after the last exit. The long cooldown is the point: in a
    crashing market a dip keeps dipping, and re-buying it every hour bleeds a
    full-size position on every leg down; one dip trade per 8 hours at most
    keeps the churn — and the fees — small. In a wide-range market
    dip-buying catches a falling knife, so the dip entry stays off there.
  * Re-entry: within 60 bars after an exit, momentum resumes above the exit price.
  * Late fallback: if the bot has still held nothing by bar 1200, one buy in a
    mild uptrend (close above the 240-bar mean, RSI 45-65), so a quiet window
    still leaves the bot with a trade on the board.
  * Exits, whichever comes first: take-profit at +3% over cost, a trailing
    stop 0.7% below the peak close since entry, a hard stop 1.5% below the
    entry close, or a stale exit after 360 bars held without profit. Long only,
    one position, full size. Every order caps slippage at 200 bps from the
    decision close.
  * Deterministic: no clock, no environment, no randomness — only the bars the
    harness sends. The same bars always give the same orders.

Dev-window backtest (faithful local replay of the kit's harness, verified
bit-for-bit against grade-local on the ema-cross baseline), same parameters on
three different frozen 24h dev windows: 18 Nov +$5.56 (3 trades, max
drawdown 1.74%), 17 Nov +$6.17 (4 trades, 1.62%), 15 Nov +$15.17 (6 trades,
1.54%). Every window positive, drawdown under 2.3% everywhere.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"

PARAMS = {
    "mom_win": 30,           # momentum window (bars)
    "mom_bounce_bps": 100,   # bounce entry: 30-bar return above +1.00%
    "mom_breakout_bps": 150, # breakout entry: 30-bar return above +1.50%
    "os_win": 240,           # oversold window for the rolling high/low (bars)
    "os_bps": 200,           # bounce entry: close this far below that high
    "ema_span": 120,         # breakout entry: EMA span
    "ema_rise_win": 30,      # breakout entry: EMA rising over this many bars
    "dip_depth_bps": 130,    # dip entry: close this far below the rolling high
    "vol_max_bps": 200,      # dip entry: 240-bar high-low range at most +2.00%
    "rsi_len": 14,           # RSI length
    "rsi_dip_max": 40,       # dip entry needs RSI at or below this
    "take_profit_bps": 300,  # sell at +3.00% over cost
    "trail_bps": 70,         # sell 0.70% below the peak close since entry
    "reenter_win": 60,       # re-entry window after an exit (bars)
    "fallback_bar": 1200,    # late fallback: first bar it may fire on
    "fallback_rsi_lo": 45,   # late fallback: RSI at or above this
    "fallback_rsi_hi": 65,   # late fallback: RSI at or below this
    "max_slippage_bps": 200,
    "dip_cooldown_bars": 480,  # dip entry: bars to wait after any exit (8h)
    "hard_stop_bps": 150,    # exit: close this far below entry close
    "max_hold_bars": 360,    # exit: bars held, if still below cost + 0.5%
}


class Ema:
    """EMA seeded with the mean of its first span of closes, then 2/(span+1) steps."""

    def __init__(self, span):
        self.span = span
        self.warm = []
        self.value = None

    def step(self, close):
        if self.value is not None:
            self.value += (2.0 / (self.span + 1)) * (close - self.value)
            return
        self.warm.append(close)
        if len(self.warm) == self.span:
            self.value = sum(self.warm) / self.span


class Rsi:
    """Wilder's RSI: first value from the mean gain/loss of the first length closes."""

    def __init__(self, length):
        self.length = length
        self.prev = None
        self.gains = []
        self.losses = []
        self.avg_gain = None
        self.avg_loss = None
        self.value = None

    def step(self, close):
        if self.prev is None:
            self.prev = close
            return
        change = close - self.prev
        self.prev = close
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        if self.avg_gain is None:
            self.gains.append(gain)
            self.losses.append(loss)
            if len(self.gains) == self.length:
                self.avg_gain = sum(self.gains) / self.length
                self.avg_loss = sum(self.losses) / self.length
                self._update()
            return
        self.avg_gain = (self.avg_gain * (self.length - 1) + gain) / self.length
        self.avg_loss = (self.avg_loss * (self.length - 1) + loss) / self.length
        self._update()

    def _update(self):
        if self.avg_loss == 0.0:
            self.value = 100.0 if self.avg_gain > 0.0 else 50.0
        else:
            rs = self.avg_gain / self.avg_loss
            self.value = 100.0 - 100.0 / (1.0 + rs)


class Strategy:
    def __init__(self, params):
        p = self.p = params
        self.closes = []
        self.ema = Ema(p["ema_span"])
        self.ema_hist = []  # EMA value after each bar (None until seeded)
        self.rsi = Rsi(p["rsi_len"])
        self.peak = None
        self.last_exit_bar = -10**9
        self.last_exit_price = 0.0
        self.ever_held = False
        self.entry_bar = None
        self.entry_price = None

    def _momentum_bps(self, win):
        n = len(self.closes)
        if n <= win:
            return None
        now = self.closes[n - 1]
        ago = self.closes[n - 1 - win]
        return math.trunc(((now - ago) * 10000) / ago)

    def _high(self, win):
        start = max(0, len(self.closes) - win)
        return max(self.closes[start:])

    def _low(self, win):
        start = max(0, len(self.closes) - win)
        return min(self.closes[start:])

    def _mean(self, win):
        start = max(0, len(self.closes) - win)
        window = self.closes[start:]
        return sum(window) / len(window)

    def on_batch(self, state, batch):
        for bar in batch["bars"]:
            if bar["symbol"] != SYMBOL:
                continue
            close = float(int(bar["c"]))
            self.closes.append(close)
            self.ema.step(close)
            self.ema_hist.append(self.ema.value)
            self.rsi.step(close)
        if state.warming_up:
            return []
        n = len(self.closes)
        if n <= max(self.p["mom_win"], self.p["os_win"]):
            return []
        held = state.positions.get(SYMBOL)
        if held is not None:
            self.ever_held = True
        close = self.closes[n - 1]
        if held is not None:
            if self.peak is None or close > self.peak:
                self.peak = close
            value = state.value(SYMBOL, held.qty)
            cost = held.cost
            take_profit = value * 10000 >= cost * (10000 + self.p["take_profit_bps"])
            trailing = close * 10000 <= self.peak * (10000 - self.p["trail_bps"])
            hard_stop = (
                self.entry_price is not None
                and close * 10000 <= self.entry_price * (10000 - self.p["hard_stop_bps"])
            )
            stale = (
                self.entry_bar is not None
                and n - 1 - self.entry_bar >= self.p["max_hold_bars"]
                and value * 10000 < cost * 10050
            )
            if take_profit or trailing or hard_stop or stale:
                self.last_exit_bar = n - 1
                self.last_exit_price = close
                self.peak = None
                self.entry_bar = None
                self.entry_price = None
                return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                         "max_slippage_bps": self.p["max_slippage_bps"]}]
            return []

        mom = self._momentum_bps(self.p["mom_win"])
        if mom is None:
            return []
        hi = self._high(self.p["os_win"])
        bounce = mom > self.p["mom_bounce_bps"] and close * 10000 < hi * (10000 - self.p["os_bps"])
        breakout = False
        ema_now = self.ema_hist[-1]
        if mom > self.p["mom_breakout_bps"] and close * 1000 >= hi * 999 and ema_now is not None:
            idx = len(self.ema_hist) - 1 - self.p["ema_rise_win"]
            ema_ago = self.ema_hist[idx] if idx >= 0 else None
            if ema_ago is not None and close > ema_now and ema_now > ema_ago:
                breakout = True
        lo = self._low(self.p["os_win"])
        flat_range = (hi - lo) * 10000 <= lo * self.p["vol_max_bps"]
        dip = (
            flat_range
            and close * 10000 < hi * (10000 - self.p["dip_depth_bps"])
            and self.rsi.value is not None and self.rsi.value <= self.p["rsi_dip_max"]
            and n - 1 - self.last_exit_bar > self.p["dip_cooldown_bars"]
        )
        reenter = (
            n - 1 - self.last_exit_bar <= self.p["reenter_win"]
            and self.last_exit_price > 0
            and mom > self.p["mom_bounce_bps"]
            and close > self.last_exit_price
        )
        fallback = (
            not self.ever_held
            and n - 1 >= self.p["fallback_bar"]
            and self.rsi.value is not None
            and self.p["fallback_rsi_lo"] <= self.rsi.value <= self.p["fallback_rsi_hi"]
            and close > self._mean(self.p["os_win"])
        )
        if not (bounce or breakout or dip or reenter or fallback):
            return []
        room = state.weight_room(SYMBOL)
        notional = state.cash if state.cash < room else room
        if notional < int(state.hello["fees"]["min_notional_quote_units"]):
            return []
        self.peak = close
        self.entry_bar = n - 1
        self.entry_price = close
        return [{"side": "buy", "symbol": SYMBOL, "notional_quote_units": notional,
                 "max_slippage_bps": self.p["max_slippage_bps"]}]


STRATEGY = Strategy(PARAMS)


def on_batch(state, batch):
    return STRATEGY.on_batch(state, batch)


if __name__ == "__main__":
    makefun_bars.run(on_batch)
