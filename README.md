# SignalFlow

SignalFlow is a premium Discord alert-routing bot for trading communities. It routes analyst alerts to users who opt in, tracks entries users actually take, and only sends matching trim, add, roll, close, and stop updates to users with relevant open positions.

SignalFlow does not place trades, connect to brokerages, use self-botting, or provide financial advice.

## Features

- Discord slash commands built with `discord.py` app commands
- Mobile-friendly premium embeds
- Analyst subscription buttons with an emoji key
- Admin dashboard for server setup and maintenance
- Server-specific classifier examples from CSV or TXT files
- AI classifier with strict confidence handling and local fast-paths for obvious alerts
- Option, stock, and futures alert detection
- Entry tracking with `Took Trade` and `Manage Alerts`
- Trim/close updates only for users who took the matching trade
- Position memory for analyst entries, adds, average up/down, option rolls, closes, and stops
- Multiple alert channels per analyst, while still supporting a single channel
- Code-generated premium recap cards for analyst stats/results
- Analyst statistics for closed trades, win rate, average win/loss, stop-out rate, and open trades
- Lightweight local web dashboard backed by the same SQLite database
- SQLite database for local testing and lightweight hosting

## Install On Windows

```powershell
cd "C:\Users\Connor\Documents\Trading Alert Bot"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
.\.venv\Scripts\Activate.ps1
```

## Environment

Copy `.env.example` to `.env`:

```powershell
Copy-Item .env.example .env
```

Minimum local setup:

```env
DISCORD_BOT_TOKEN=your_discord_bot_token_here
DATABASE_PATH=signalflow.sqlite3
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_CLASSIFIER_MODEL=gpt-5-mini
USE_AI_CLASSIFIER=true
USE_IMAGE_CLASSIFIER=true
```

Optional values:

```env
GUILD_ID=123456789012345678
OWNER_IDS=123456789012345678
AUTO_TAKE_USER_IDS=123456789012345678
CLEAR_GUILD_COMMANDS=false
OPENAI_CLASSIFIER_TIMEOUT_SECONDS=8
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8080
ENABLE_WEB_DASHBOARD=false
DASHBOARD_TOKEN=
PUBLIC_DASHBOARD_URL=http://127.0.0.1:8080
DASHBOARD_SESSION_SECRET=change_this_to_a_long_random_string
DISCORD_CLIENT_ID=
DISCORD_CLIENT_SECRET=
DISCORD_OAUTH_REDIRECT_URI=http://127.0.0.1:8080/oauth/callback
```

`GUILD_ID` makes slash command syncing faster while testing one server. Leave it blank for global commands.

## Run Locally

```powershell
cd "C:\Users\Connor\Documents\Trading Alert Bot"
.\.venv\Scripts\Activate.ps1
py main.py
```

The first run creates the SQLite database.

To run the bot and dashboard together locally:

```powershell
$env:ENABLE_WEB_DASHBOARD="true"
py main.py
```

## Run The Web Control Panel

The web control panel reads the same SQLite database as the bot. It shows analyst performance and lets server admins manage analysts, channel mappings, review channel, classifier examples, open position memory, bot access, and branding settings.

```powershell
cd "C:\Users\Connor\Documents\Trading Alert Bot"
.\.venv\Scripts\Activate.ps1
py web_dashboard.py
```

Open:

```text
http://127.0.0.1:8080
```

The web dashboard now supports Discord OAuth login. Add these values to `.env` to enable it:

```env
DISCORD_CLIENT_ID=your_application_client_id
DISCORD_CLIENT_SECRET=your_application_client_secret
DISCORD_OAUTH_REDIRECT_URI=http://127.0.0.1:8080/oauth/callback
DASHBOARD_SESSION_SECRET=use_a_long_random_string_here
```

In the Discord Developer Portal, open your application, go to **OAuth2**, and add this exact redirect URL:

```text
http://127.0.0.1:8080/oauth/callback
```

For hosting, change both Discord and `.env` to your public domain:

```text
https://yourdomain.com/oauth/callback
```

OAuth uses the `identify` and `guilds` scopes. A logged-in Discord user can only open dashboards for configured servers where they are the owner, have Administrator, or have Manage Server permission. Discord IDs in `OWNER_IDS` can see every configured server.

Inside Discord, a server admin can run:

```text
/admin_web_link
```

That returns a private per-server dashboard link:

```text
http://127.0.0.1:8080/?guild_id=SERVER_ID&token=SERVER_TOKEN
```

Private links still work as a fallback. Before hosting this publicly, set `PUBLIC_DASHBOARD_URL` to your real domain and optionally keep a global owner token in `DASHBOARD_TOKEN`:

```text
PUBLIC_DASHBOARD_URL=https://yourdomain.com
DASHBOARD_TOKEN=owner_only_master_token
```

## Railway Deployment

This repo includes a `Procfile`, so Railway can start the app with:

```text
python main.py
```

On Railway, `main.py` automatically starts both the Discord bot and the web dashboard when Railway provides a `PORT` variable. Set these Railway variables:

```env
DISCORD_BOT_TOKEN=your_discord_bot_token
DATABASE_PATH=/data/signalflow.sqlite3
OWNER_IDS=your_discord_user_id
OPENAI_API_KEY=your_openai_api_key
OPENAI_CLASSIFIER_MODEL=gpt-5-mini
USE_AI_CLASSIFIER=true
USE_IMAGE_CLASSIFIER=true
ENABLE_WEB_DASHBOARD=true
DISCORD_CLIENT_ID=your_discord_application_client_id
DISCORD_CLIENT_SECRET=your_discord_application_client_secret
PUBLIC_DASHBOARD_URL=https://your-railway-domain.up.railway.app
DISCORD_OAUTH_REDIRECT_URI=https://your-railway-domain.up.railway.app/oauth/callback
DASHBOARD_SESSION_SECRET=long_random_secret
DASHBOARD_TOKEN=optional_owner_master_token
```

Do not set `DASHBOARD_HOST` on Railway unless you set it to:

```env
DASHBOARD_HOST=0.0.0.0
```

If you accidentally copy `DASHBOARD_HOST=127.0.0.1` to Railway, SignalFlow will still bind to `0.0.0.0` when Railway provides `PORT`.

In the Discord Developer Portal, add the Railway callback URL exactly:

```text
https://your-railway-domain.up.railway.app/oauth/callback
```

For paid servers, attach a Railway volume mounted at `/data` or move to Postgres before launch. Plain SQLite without persistent storage can disappear on redeploys.

If Discord login returns you to the login page, check these first:

- `OWNER_IDS` must include your Discord user ID if you want full owner access.
- `DASHBOARD_SESSION_SECRET` must be set to one stable long random value and should not change between deploys.
- `PUBLIC_DASHBOARD_URL` must be the Railway HTTPS domain, without `/login` or any path at the end.
- After changing Railway variables, redeploy the service.

## Discord Setup

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create an application and add a bot.
3. Copy the bot token into `.env`.
4. Enable **Message Content Intent** under Bot privileged gateway intents.
5. In OAuth2 URL Generator, select `bot` and `applications.commands`.
6. Give the bot permission to view channels, send messages, read message history, and use slash commands.
7. Invite it to your test server.

## Server Setup

Use `/admin_dashboard` for the main setup menu.

Typical setup flow:

```text
/admin_dashboard
Analysts -> Add Analyst
Analysts -> Map Channel
Analysts -> Review Channel
/select_analysts
```

Use `Analysts -> Map Channel` again with the same analyst and another channel to add more routed channels:

```text
#analyst-options
#analyst-futures
#analyst-stocks
```

If a channel is already mapped to another analyst, SignalFlow reassigns that channel to the new analyst. Existing one-channel setups continue to work.

You can import server-specific examples with:

```text
/admin_import_examples_csv action:Entry file:entries.txt
/admin_import_examples_csv action:Trim file:trims.txt
/admin_import_examples_csv action:Close file:closes.txt
/admin_import_examples_csv action:Ignore file:ignores.txt
```

TXT files should contain one example per line. CSV files should include a `Content`, `text`, `example_text`, or `message` column.

## Commands

User commands:

- `/start`
- `/signalflow_help`
- `/select_analysts`
- `/my_alerts`
- `/current_positions`
- `/pause_alerts`
- `/resume_alerts`

Admin commands require Manage Server:

- `/admin_dashboard`
- `/admin_import_examples_csv`

Most admin actions now live inside `/admin_dashboard`: analyst setup, channel mapping, review channel, examples, position memory, and test alerts. The CSV/TXT import remains a slash command because Discord dashboard buttons cannot open a file upload picker.

Owner commands require your Discord ID in `OWNER_IDS`.

## Test Messages

Entries:

```text
BTO SPY 530C 5/24 @ 1.20
OPEN $NAVN $20 call 6/18 @ 1.80 (swing, half sized for now)
SPX 7385C - 3.5
I'm Entering
Option: MSFT 432.50 C 5/30
Entry: 3.00
Buying TSLA shares @ 210.50
Starter $IREN shares at 8.40
Long /ES 5350
short NQ @ 18750
```

Adds:

```text
added to SPY @.70
Averaging down SPY 530C at .80
Adding higher on runners, SPY 530C @ 1.50
```

Trims:

```text
taking a trim here at +50%
down to 1/3 position MSFT @1.4
3.1 - trim
3.5 - 50%
sold half TSLA shares @ 215
Trim /ES 5375
```

Closes and stops:

```text
exiting trade at B/E
stopped out here, only an 18% loss
Closed SPY here
Cut here
all out NQ 18820
```

Rolls:

```text
Rolling SPY 5/24 530C to 5/31 540C for .25 debit
$GOOGL roll these back to BTO 1/16 $320c @ 4.65
```

Ignore/review candidates:

```text
MSFT day trade idea, will alert any entry
WATCHING SPY 530C
possible NVDA calls if it breaks high
Long TSLA over 210
watching ES 5350
added here
trim?
```

## Recap Card Preview

SignalFlow includes a code-generated recap card renderer that matches the dark premium recap style without relying on AI-generated text inside images.

Generate a sample locally:

```powershell
.\.venv\Scripts\python.exe recap_renderer.py --sample --output logs\recap_sample.png
```

Generate a recap from the SQLite database. Recaps only include trades that are fully closed, stopped out, or all out; trim-only open trades are not shown.

```powershell
.\.venv\Scripts\python.exe recap_renderer.py --from-db --database signalflow.sqlite3 --brand "Evenstar Trading" --footer "Evenstar Trading | Premium Recap" --output logs\recap_today.png
```

You can also open `/admin_dashboard`, go to `Testing`, and press `Daily Recap` to generate today's recap from the server's tracked alert logs.

## Notes

- Users must opt in before receiving analyst DMs.
- Users can pause and resume DMs at any time.
- Medium-confidence alerts go to the review channel when configured.
- Review queue buttons can approve alerts as Entry, Trim, or Close, or save them as Ignore. Each button press saves that wording as a server-specific classifier example so future alerts in that server are better tailored.
- If a trim, close, or stop lacks ticker/contract details, SignalFlow uses the analyst's most recent open position.
- Stock alerts should include stock/share/common/equity wording. This keeps ordinary chart levels like `Long TSLA over 210` from becoming false alerts.
- Futures alerts support symbols like `/ES`, `ES`, `/NQ`, `NQ`, `/MNQ`, `CL`, `GC`, `YM`, and `RTY`.
- The bot uses official Discord bot APIs only.
