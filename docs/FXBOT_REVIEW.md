# fxbot/ line-by-line review

Reviewed on branch `arena/dbbef064-fx-trading-signals` (base `a274578`, 2026-11-19). Every file in
`fxbot/` and the publish scripts (`scripts/publish_signals.py`, `scripts/post_telegram.py`,
`scripts/scan.py`, `scripts/train.py`) was read in full. This review changes no code. Each item
has a status: **open** (no change made), or **decision** (needs the owner's choice first).

Severity: **High** = affects what members are shown or what a published number means. **Medium** =
correctness or security risk with a bounded blast radius. **Low** = hygiene, dead code, or performance.

## High

1. **The backtest does not test the live strategy.** `fxbot/backtest.py:126-127` builds each plan
   with `forecast=None` and no `strength` argument, so the ML factor (weight 0.20) and the
   currency-strength factor (weight 0.10) are both zero. The live engine (`fxbot/engine.py`) uses
   both. Any backtest figure describes a different strategy from the one that sends signals.
   **Open.** The backtest also applies a trailing rule (lock +1R past +2R, `backtest.py:232-234`)
   that the live plan text does not describe.

2. **A heuristic probability is shown to members as an estimate.** `fxbot/signals.py:284` sets
   `p_target = 1/(1+R:R) + 0.5 × edge`. This is a break-even probability nudged by the model, not a
   calibrated figure. It appears in the dashboard (`signals.py:306-307`), in `instructions`, and in
   the Telegram message (`fxbot/telegram.py:84`, "P(target first) N%"). The repo contains no
   calibration evidence. **Decision:** see the question on P(target first) in the review summary.

3. **Stale data can be published as live.** `fxbot/data.py:475` writes the source label as
   `as of <now>`, which is the fetch time, not the timestamp of the last bar. `engine.py:204` stamps
   `generated_at` at report time, after the fetch. The Worker's 12-hour freshness check (`worker/src/signals.js:8`, which compares `generated_at`) therefore passes
   whenever the fetch succeeds, even if the newest bar is days old (for example a stalled Yahoo
   response). Nothing compares the last bar's timestamp with the clock. **Decision:** see the
   question on the stale-data gate.

## Medium

4. **The Kelly reference uses an invented win rate.** `engine.py:294` sets
   `est_wr = mean(confidence) × 0.5 + 0.25`, then `risk.py:226-234` computes half-Kelly from it.
   The result is published as `kelly_reference_pct`. It is not measured and should not be shown as
   a sizing reference. **Open.**

5. **Conversion rates have no maximum age.** `fxbot/conversion.py:88` forward-fills the latest rate
   with no limit, and `latest_conversion_rate` feeds position sizing. A stalled feed
   can size trades with a rate days old. **Open.**

6. **Partial publish leaves pair rows and no commit marker.** `fxbot/publish.py:97` writes each
   `pair:` row before the `scan` row (`:104`, the commit marker). If a run fails between them, the
   pair rows carry a new `run_id` that does not match the old `scan` row. The Worker then returns
   503 for pair pages until the next successful run. This is safe (no stale mix) but it is an
   outage, not a rollback. **Open.**

7. **Publish size guard.** `publish.py:70-71` refuses any single statement over 90,000 bytes (`MAX_STATEMENT_BYTES`, line 30). The
   `scan` row contains `instructions` (`:104`), and `instructions` is also written on its own row
   (`:100-103`), so the payload is duplicated and grows with the universe. The guard fails safe, but
   the duplication should be removed when item 5 is restructured. **Open** (tied to item 5).

8. **The backtest endpoint blocks every scan.** `fxbot/server.py:529-576` runs `run_backtest` under
   the global `_engine_lock` (`:541`). One backtest with `bars` up to 20,000 stalls scans and pair
   reports for all users. **Open.**

9. **Two documentation claims are wrong.** `server.py:38-39` says sign-in demotes other admins.
   `auth.authenticate` never demotes anyone. Demotion runs only in `seed_admin`, at server import
   (`server.py:609` calls it at import; `auth.py:306-308`, inside `seed_admin` at `auth.py:258`). `server.py:588` says accounts "live in memory", but they
   are stored in the database. **Open.**

10. **Login and the API description allow email and phone.** `server.py:120-121` describes the
    login identifier as "username, email or phone number", and `server.py:271` says the same in the
    error text. The rule is username only (`auth.py:176`, and the Worker). **Open.**

11. **Reset-code attempts are not atomic.** `auth.py:226-241` reads `reset_attempts` and then
    writes it back in the same session, so parallel guesses can exceed the 5-attempt limit against a
    7-digit code. The window is small but the code is short. **Open.**

12. **Early stopping uses a shuffled split.** `fxbot/ml.py:60-61` sets `early_stopping=True` with
    `validation_fraction=0.1`. The scikit-learn version installed here (1.9.1) calls
    `train_test_split` with its default `shuffle=True`, so the stopping rows come from across the
    training window and not from its end. The module docstring's "strictly time-ordered" claim is
    not fully true. The reported out-of-sample accuracy (`ml.py:103-104`) is still a time-ordered
    hold-out. **Open.**

13. **The Python and Worker schemas have diverged.** `fxbot/db.py:110-125` (`AdminAuditEvent`) has
    no `event_key` column; the Worker's table has one (`worker/migrations/0001_initial.sql`). The
    passwords also differ: 200,000 PBKDF2 iterations in `auth.py:39` against 100,000 in the Worker.
    This matters only if the FastAPI backend is deployed. **Decision:** is it? If not, the code can
    be marked as local-only.

14. **FastAPI sessions are never purged.** `auth.py:332-345` deletes an expired session only when
    that token is presented again. This is the same gap as audit item 4 in the Worker, which is now
    fixed there. **Open.**

15. **Admin lists are unbounded and N+1 (FastAPI).** `billing.list_users` (`billing.py:495-508`)
    runs one grant query per user and has no limit. `list_payments` (`:314-325`) has no limit.
    **Open** (tied to item 5).

16. **Invoice creation has no uniqueness guard (FastAPI).** `billing.create_payment`
    (`:198-240`) cancels open invoices and inserts a new one in separate statements, with no unique
    index. Two requests in parallel can leave two awaiting invoices. The Worker has a unique index
    for this. **Open.**

17. **Rejecting a payment is not compare-and-set (FastAPI).** `billing.reject_payment`
    (`:369-379`) reads the status and then writes it. `confirm_payment` (`:328-346`) does the check
    in the update itself. Under READ COMMITTED (Postgres), a concurrent confirm and reject can both
    apply. **Open.**

## Low

18. **Pickle persistence is dead code.** `fxbot/ml.py:155-166` saves and loads models with pickle.
    Nothing calls either method. Remove them, or the next person to wire them up will load untrusted
    pickles. **Open.**

19. **Per-scan repeated work.** `compute_all` runs several times per pair per scan (`engine.py:436`
    in `pair_report`; `signals.py:167` when `build_plan` is called without `precomputed`;
    `features.py:22` in both fit and forecast). `indicators.py:133-138` computes CCI with a Python
    lambda over every rolling window. Performance matters for the GitHub job's run time. **Open.**

20. **Model cache lives for the process.** `ml.py:172-190` trains each symbol once and reuses the
    model for as long as the process runs. The server keeps it for its whole uptime, so it is never
    retrained. **Open.**

21. **Redundant feature.** `features.py:83` `log_ret_1` duplicates `ret_1` (built in the loop at `features.py:30`). **Open.**

22. **Duplicated parameter.** `min_risk_reward` is defined in both `RiskConfig` (`config.py:23`) and
    `SignalConfig` (`config.py:51`) and can drift. **Open.**

23. **Relative cache path.** `config.py:86` sets `cache_dir = "data/cache"`, which depends on the
    working directory. **Open.**

24. **Hard-coded database name.** `publish.py:31` sets `DATABASE = "klev"`. It should be read from
    `worker/wrangler.jsonc`. **Open.**

25. **File-descriptor leak.** `scripts/publish_signals.py:41` opens a temporary file with
    `mkstemp` and keeps only the path. **Open.**

26. **Unbounded response read.** `data.py:238-246` reads the whole provider response with no size
    cap. The timeout is 8 seconds (`config.py:85`), which is short for Yahoo under load. **Open.**

27. **Currency strength uses last bars that may not align.** `strength.py:51` uses `iloc[-1]` per
    symbol. If feeds end at different times, the cross-sectional strength mixes times. **Open.**

28. **The backtest's Sharpe ratio assumes hourly bars.** `backtest.py:303` annualises with
    `252 × 24`. On the daily timeframe this is wrong by a factor of 24. **Open.**

29. **The daily Frankfurter frame builds high and low from closes.** `data.py:248-257` sets
    `high`/`low` to rolling closes, not real intraday values. ATR and stops on daily data are
    therefore understated. This path runs only for the 1d timeframe, which is not the default.
    **Open.**

30. **`/api/health` is public and returns the full engine config.** `server.py:469-478`. The config
    is not secret, but it reveals the tuning. **Open.**

31. **Unused indicators.** `indicators.py:148` (`obv`, which has no volume data) and the
    functions `wma`, `keltner` and `ichimoku` are not used on the signal path. Remove them or mark
    them as reference. **Open.**

32. **Minor parsing.** `auth.py:382` calls `int()` on the SMTP port without a guard. `server.py:592`
    imports `os` again inside a function. **Open.**

## Checked and sound

- Signal geometry: stop, first target and runner target are correct for both BUY and SELL
  (`signals.py:247-276`). Structure refinement only moves the stop in a sensible direction.
- No look-ahead in indicators or features: every feature uses data at or before bar t
  (`features.py` docstring; `indicators.py` rolling windows). Swing points use a centred window, but
  a swing is confirmed only `order` bars later, so the latest bar never uses future prices.
- Donchian uses the current bar, so the breakout score stays within [-1, 1]
  (`indicators.py:96-99`, `signals.py:89-97`).
- Labels are never used as features (`features.py:88-101`). The training purge removes the last
  `horizon` rows before the hold-out (`ml.py:89-94`).
- Backtest entries use the next bar's open; the stop wins when stop and target are both touched in
  one bar; slippage is applied to every fill; conversion uses lag-1 rates (`backtest.py:120-127`,
  `conversion.py:64-69`).
- Risk sizing converts the stop distance into account currency before dividing
  (`risk.py:61-70`); the drawdown factor is applied once (`risk.py:152-155`, `:178`).
- Live data is all-or-nothing for FX, and a daily-only provider is never used on the hourly
  timeframe (`data.py:353-430`).
- The Telegram state key `posted` and the disclaimer text match the Worker
  (`fxbot/telegram.py:21-23` and `worker/src/telegram.js:15,18`).
- Payment confirmation is a compare-and-set update before the grant is written
  (`billing.py:328-366`). The bootstrap admin cannot be reset through the API (`billing.py:448-449`).
- Reset codes are stored hashed and cleared after a success; a successful reset ends all sessions
  (`auth.py:243-249`).

## Summary of decisions needed

- **Item 5 (signal reads).** Options: (A) store `meta` and per-pair rows so each request reads one
  small row, plus `limit`/`offset` on admin lists; (B) add a Workers KV cache keyed by `run_id`
  over the current D1 layout; (C) both; (D) leave as is.
- **P(target first) (item 2).** Options: remove it from the site and Telegram; keep it and label it
  "model estimate, not calibrated"; or calibrate it on backtest outcomes before showing it.
- **Stale-data gate (item 3).** Options: refuse the publish when the newest bar is older than 3
  hours (may block weekend publishes); refuse when older than 36 hours (weekend-tolerant); or only
  label the bar time.
- **FastAPI backend (item 13).** Is it deployed anywhere? If not, mark it local-only in its
  docstring.
