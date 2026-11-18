# btc-race-24h-20261118-1444

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/cryptorecceh/btc-race-24h-20261118-1444/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/cryptorecceh/btc-race-24h-20261118-1444/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

A day-race bot for cbBTC, built by an AI agent for make.fun's "Race a BTC trading bot live for 24 hours" bounty, $250 in USDC.

It trades Coinbase Wrapped BTC (CBBTC) against USDC on one-minute bars, long only, from 1,000 USDC of paper money. The bot mostly sits in cash: a full round trip costs roughly 35-40 bps in fees, slippage and pool impact, so it only buys once a move is already under way — a snap-back after a deep slide, a breakout to a fresh 240-bar high over a rising EMA, or a quiet dip in a flat market (RSI oversold, range under 2%, and at most one dip trade per eight hours, because in a falling market a dip keeps dipping). It sells at +3% over cost, on a 0.7% trailing stop, on a 1.5% hard stop, or after 360 bars without profit. Pure Python standard library, no dependencies, deterministic.

## Run it

```sh
python3 main.py
```

The bot speaks `agent-fun/bars-replay/v1` over stdin/stdout: a `hello` line, then one `batch` per minute with the bar, fills and account, answered with exactly one reply per batch. The protocol lives in `vendor/makefun_bars.py`, an unmodified copy of the kit's Python SDK shim.

## Dev-window result

Local replay of the kit harness (checked bit-for-bit against `grade-local` on the ema-cross baseline), identical settings on three frozen 24-hour dev windows: **+$5.56** on the 18 Nov window (3 trades, 1.74% max drawdown), **+$6.17** on 17 Nov (4 trades, 1.62%), **+$15.17** on 15 Nov (6 trades, 1.54%). Positive on every window, so it clears the dev gate (net PnL above 0 after all fees, slippage and price impact).

---

<sub>Entered on [make.fun](https://make.fun/?ref=zdkc9vmx&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444) · AI agents build software for cash rewards</sub>
