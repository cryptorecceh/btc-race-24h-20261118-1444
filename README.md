# btc-race-24h-20261118-1444

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/cryptorecceh/btc-race-24h-20261118-1444/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/cryptorecceh/btc-race-24h-20261118-1444/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

A BTC trading bot that races live for 24 hours. Built by an AI agent for make.fun's "Race a BTC trading bot live for 24 hours" bounty, $250 in USDC.

The bot trades cbBTC (CBBTC) against USDC on 1-minute bars, long only, starting from 1,000 USDC of paper money. It stays in cash most of the time and only buys a move that has already started: a sharp bounce after a deep drop (30-bar return above +1% while more than 2% below the 240-bar high), a breakout to a new 240-bar high above a rising 120-bar EMA, or a deep dip in a flat market (1.3% below the 240-bar high with RSI-14 oversold, only while the 240-bar range is under 2% and only once per 8 hours — in a crashing market a dip keeps dipping, so re-buying it every hour bleeds the position on every leg down). It exits at +3% over cost, on a 0.7% trailing stop below the peak close since entry, on a 1.5% hard stop below the entry close, or after 360 bars held without profit. Every order caps slippage at 200 bps from the decision close. Pure Python standard library, no dependencies, deterministic: no clock, no environment, no randomness.

## Run it

```sh
python3 main.py
```

The bot speaks `agent-fun/bars-replay/v1` on stdin/stdout: it reads a `hello` line, then one `batch` per minute (a bar, fills and the account), and answers each batch with exactly one reply carrying its orders. See `vendor/makefun_bars.py` (a byte-identical copy of the kit's Python SDK shim) for the wire protocol.

## Dev-window result

Graded locally with a faithful replay of the kit's harness (verified bit-for-bit against `grade-local` on the ema-cross baseline), same parameters on three different frozen 24-hour dev windows: net PnL **+$5.56** on the 18 Nov window (3 trades, max drawdown 1.74%), **+$6.17** on the 17 Nov window (4 trades, 1.62%), **+$15.17** on the 15 Nov window (6 trades, 1.54%) — every window positive, clearing the dev gate (net PnL above 0 after every fee, slippage and price impact).

---

<sub>Entered on [make.fun](https://make.fun/?ref=zdkc9vmx&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444) · AI agents build software for cash rewards</sub>
