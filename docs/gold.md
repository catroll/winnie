# Gold price watch

Status: first implementation. The sampler, the rules, the conversation record and
the push path are implemented and tested; a real alert waits for the market to
move. Last updated: 2026-11-01.

## 1. Goal

The owner (`cleanup`) asked for a feature that watches the gold price and
notifies them through the Winnie app when it moves sharply in a short time. The
alert has to be useful on a lock screen: a price, the size of the move, the
window, and a source, not a chart or a paragraph of analysis.

## 2. Source

`https://api.gold-api.com/price/XAU` returns one quote:

```json
{"symbol":"XAU","currency":"USD","price":3876.39,"updatedAt":"2026-10-31T22:28:00Z"}
```

It needs no key and has no history, so the watcher samples it every five minutes
and keeps its own samples in `.state/gold.json`. The quote's `updatedAt` is used
as the sample time, so a stale upstream timestamp does not become a fresh
sample.

The price is **USD per troy ounce**. A CNY-per-gram view needs an FX rate and a
unit conversion; that is deliberately out of scope until the owner asks for it.

## 3. Rules

| Setting | Default | Meaning |
|---|---|---|
| `schedule` | `every 5m` | sampling cadence |
| `window_minutes` | 60 | short window |
| `threshold_percent` | 1.0 | a move this large in the window alerts |
| `daily_threshold_percent` | 2.5 | a 24-hour move this large alerts even if the hour was quiet; 0 disables |
| `cooldown_minutes` | 120 | no second alert in this window, whichever direction |
| `history_hours` | 48 | samples older than this are pruned |

The baseline is the sample closest to one window ago, searched between 50 % and
200 % of the window. A move measured over 45 minutes is labelled "过去 45 分钟",
not rounded up to an hour. With no sample old enough, the check stays silent:
the first possible alert is about 30 minutes after the watcher starts, and a
60-minute change needs about an hour of uptime. Samples persist, so a restart
does not reset the window.

The 24-hour rule uses a sample between 20 and 48 hours old. It catches a slow
grind that never moves 1 % in an hour.

## 4. Delivery

A move produces two records:

1. **A conversation** with `source: gold`, `channel: notification`, titled
   "黄金价格波动提醒". It is the durable copy, indexed like any other note.
2. **A push notification** through the assistant's `Notifier`, which the host
   implements over the push service (`PushNotifierAdapter`). That is the same
   path as the assistant's `notify_user` tool and the scheduled-task briefs: a
   database row, a WebSocket to the phone, and a notification on Android.

The owner is resolved by the host: with accounts enabled it is the only active
account (`cleanup` on this instance); a multi-account deployment must set
`phone.owner_user`. The assistant never receives the user id from the model.

Cooldown is per watcher, not per direction: after an alert up, a reversal within
two hours does not immediately produce a second noise. The state records the
last alert time and price for the console and for the next decision.

`POST /assistant/gold/check` is a manual inspection: it samples and reports,
but it never pushes and it does not move the polling schedule. A test or a
console refresh must not train the owner to ignore alerts.

## 5. Configuration

```yaml
assistant:
  gold:
    enabled: true
    symbol: XAU
    schedule: every 5m
    window_minutes: 60
    threshold_percent: 1.0
    daily_threshold_percent: 2.5
    cooldown_minutes: 120
    history_hours: 48
    timeout_seconds: 15
    tick_seconds: 30
```

The skill is called `gold` and exposes one tool, `gold_price`, which returns the
current quote, the window and day changes, and the rules. The console's Skills
page toggles the feature; disabling it removes the tool and makes the watcher
skip its next poll.

The watcher runs in the API process like the weather watch, so it only samples
while the API is up. A gap in samples is visible in the state file (`last_poll`)
and simply delays the next possible window comparison.

## 6. Limits

- **One quote every five minutes is not a tick feed.** A spike that reverses
  inside a sampling interval is invisible. The feature is for "large move over
  tens of minutes", not for trading.
- **The source is free and keyless.** It can rate-limit, change shape or stop.
  A failed poll records `last_status: error` and does not affect the last good
  samples; a real provider is a new `GoldService` implementation.
- **USD per ounce only.** No CNY/gram, no premiums, no physical-versus-futures
  distinction.
- **No market calendar.** The watch samples around the clock; a quiet weekend
  simply produces no move.
- **No per-user settings.** The threshold and window are deployment settings,
  shared by every account on the instance; this instance has one.
- **Push needs the app's connection.** If the phone is offline the event is in
  the push backlog and arrives on reconnect; if the app's foreground service is
  force-stopped there is no delivery until it runs again.
