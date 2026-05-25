# Binance Spot Trading Bot PM2 Restart Guide

This guide explains how to safely restart the Binance Spot Trading Bot after pulling changes from GitHub, editing the `.env` file, installing dependencies, or making any other application changes.

The application is managed by **PM2** and runs using the Python virtual environment located inside the project folder.

## 1. Project location

The application is located at:

```bash
/home/admin/apps/derrick/Binance-Spot-Trading-Bot
```

Always start by moving into the project directory:

```bash
cd /home/admin/apps/derrick/Binance-Spot-Trading-Bot
```

Activate the Python virtual environment:

```bash
source .venv/bin/activate
```

Confirm that the correct Python is being used:

```bash
which python
```

Expected output should look like this:

```bash
/home/admin/apps/derrick/Binance-Spot-Trading-Bot/.venv/bin/python
```

---

# 2. Check current PM2 status

Before making changes, check whether the bot is currently running:

```bash
pm2 status
```

Look for:

```text
trading-bot-web
```

A healthy state should show:

```text
online
```

You can also inspect logs:

```bash
pm2 logs trading-bot-web
```

To show more lines:

```bash
pm2 logs trading-bot-web --lines 100
```

---

# 3. Restarting after pulling changes from GitHub

Use this process when the source code has changed on GitHub.

## Step 1: Go to the project directory

```bash
cd /home/admin/apps/derrick/Binance-Spot-Trading-Bot
```

## Step 2: Check current Git status

```bash
git status
```

This shows whether there are local changes.

If only expected files like `.env`, `data/`, `outputs/`, or logs are changed, make sure they are ignored by Git.

Your `.gitignore` should include runtime files like:

```gitignore
.env
data/
outputs/
logs/
.tmp/
.venv/
__pycache__/
*.pyc
```

Important: `.env` should usually not be committed because it may contain API keys, secrets, database settings, and trading configuration.

## Step 3: Pull the latest code

```bash
git pull origin main
```

If your branch is not `main`, check it with:

```bash
git branch
```

Then pull from the correct branch, for example:

```bash
git pull origin master
```

or:

```bash
git pull origin production
```

## Step 4: Activate the virtual environment

```bash
source .venv/bin/activate
```

## Step 5: Reinstall the project

Because the project was installed in editable mode, many code changes are picked up automatically. However, after pulling changes, it is still good practice to reinstall the package in case `pyproject.toml` or dependencies changed.

```bash
python -m pip install -e ".[web]"
```

If dependencies changed, upgrade them too:

```bash
python -m pip install --upgrade -e ".[web]"
```

## Step 6: Restart the PM2 app

```bash
pm2 restart trading-bot-web
```

## Step 7: Check logs

```bash
pm2 logs trading-bot-web --lines 100
```

You should see something similar to:

```text
Application startup complete.
Uvicorn running on http://0.0.0.0:8880
web_dashboard_started port=8880
```

## Step 8: Test the dashboard locally

```bash
curl http://127.0.0.1:8880
```

If the server responds, the app is running.

---

# 4. Restarting after changing `.env`

Use this when you modify environment variables such as API keys, ports, database paths, trading mode, secrets, Binance credentials, or strategy settings.

## Step 1: Open the `.env` file

```bash
cd /home/admin/apps/derrick/Binance-Spot-Trading-Bot
nano .env
```

or:

```bash
vim .env
```

## Step 2: Save the file

After editing, save and close the file.

## Step 3: Ensure correct ownership and permissions

The `.env` file should be readable by the `admin` user running PM2.

```bash
sudo chown admin:sudo .env
chmod 600 .env
```

Check it:

```bash
ls -al .env
```

Expected style:

```text
-rw------- 1 admin sudo ... .env
```

## Step 4: Restart the app

PM2 does not automatically reload `.env` changes unless the app itself watches and reloads them. For this bot, restart it manually:

```bash
pm2 restart trading-bot-web
```

## Step 5: Confirm startup

```bash
pm2 logs trading-bot-web --lines 100
```

Look for:

```text
Application startup complete.
web_dashboard_started
```

## Important note about `.env`

Changing `.env` is like changing the app’s instruction sheet. The running process already read the old instructions when it started. Restarting tells PM2 to launch the app again so it reads the new `.env` values.

---

# 5. Restarting after changing `ecosystem.config.js`

Use this when you change PM2 settings such as:

```js
script
args
cwd
env
error_file
out_file
max_memory_restart
restart_delay
```

After editing `ecosystem.config.js`, restart using the ecosystem file:

```bash
cd /home/admin/apps/derrick/Binance-Spot-Trading-Bot
pm2 restart ecosystem.config.js
```

Then save the new PM2 process configuration:

```bash
pm2 save
```

Check status:

```bash
pm2 status
```

Check logs:

```bash
pm2 logs trading-bot-web --lines 100
```

---

# 6. Restarting after dependency changes

Dependency changes usually happen when these files change:

```text
pyproject.toml
requirements.txt
setup.py
setup.cfg
```

For this project, the key file is likely:

```text
pyproject.toml
```

After pulling changes that affect dependencies, run:

```bash
cd /home/admin/apps/derrick/Binance-Spot-Trading-Bot
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install --upgrade -e ".[web]"
pm2 restart trading-bot-web
```

Then verify:

```bash
pm2 logs trading-bot-web --lines 100
```

You can also list installed packages:

```bash
python -m pip list
```

---

# 7. Full safe update procedure

This is the recommended full procedure after pulling changes from GitHub.

```bash
cd /home/admin/apps/derrick/Binance-Spot-Trading-Bot

git status
git pull origin main

source .venv/bin/activate

python -m pip install --upgrade pip setuptools wheel
python -m pip install --upgrade -e ".[web]"

pm2 restart trading-bot-web

pm2 status
pm2 logs trading-bot-web --lines 100

curl http://127.0.0.1:8880
```

If your branch is not `main`, replace:

```bash
git pull origin main
```

with the correct branch.

---

# 8. Quick restart only

Use this when you have not changed code or dependencies, but you simply want to restart the running app.

```bash
pm2 restart trading-bot-web
```

Check status:

```bash
pm2 status
```

Check logs:

```bash
pm2 logs trading-bot-web --lines 50
```

---

# 9. Stop and start manually

To stop the bot:

```bash
pm2 stop trading-bot-web
```

To start it again:

```bash
pm2 start trading-bot-web
```

If PM2 cannot find it by name, start from the ecosystem file:

```bash
cd /home/admin/apps/derrick/Binance-Spot-Trading-Bot
pm2 start ecosystem.config.js
```

---

# 10. Restart all PM2 apps

Only use this if you intentionally want to restart every app managed by PM2:

```bash
pm2 restart all
```

Since this server runs multiple apps, prefer restarting only the trading bot:

```bash
pm2 restart trading-bot-web
```

---

# 11. Save PM2 process list

Whenever you add, remove, rename, or reconfigure a PM2 app, save the PM2 process list:

```bash
pm2 save
```

This ensures the current PM2 process list is restored after reboot.

---

# 12. Enable PM2 startup after server reboot

Run:

```bash
pm2 startup
```

PM2 will print a command that starts with something like:

```bash
sudo env PATH=...
```

Copy and run the exact command PM2 gives you.

Then save the process list:

```bash
pm2 save
```

After this, PM2 should automatically restart the bot when the server reboots.

---

# 13. Confirm reboot recovery

To test whether the app survives a reboot:

```bash
sudo reboot
```

After the server comes back online:

```bash
pm2 status
```

Then:

```bash
pm2 logs trading-bot-web --lines 100
```

Test the dashboard:

```bash
curl http://127.0.0.1:8880
```

---

# 14. Recommended `ecosystem.config.js`

This is the recommended PM2 configuration for this bot:

```js
/**
 * PM2 Ecosystem Configuration for the Trading Bot.
 *
 * Usage:
 *   pm2 start ecosystem.config.js
 *   pm2 status
 *   pm2 logs trading-bot-web
 *   pm2 restart trading-bot-web
 *   pm2 stop trading-bot-web
 *
 * The web dashboard runs on port 8880 by default.
 */
module.exports = {
  apps: [
    {
      name: "trading-bot-web",

      cwd: "/home/admin/apps/derrick/Binance-Spot-Trading-Bot",

      script: "/home/admin/apps/derrick/Binance-Spot-Trading-Bot/.venv/bin/python",

      args: "-m uvicorn app.web.server:app --host 0.0.0.0 --port 8880",

      interpreter: "none",

      env: {
        PYTHONUNBUFFERED: "1",
        WEB_PORT: "8880",
      },

      max_restarts: 10,
      restart_delay: 5000,
      autorestart: true,

      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
      error_file: "/home/admin/apps/derrick/Binance-Spot-Trading-Bot/logs/pm2-web-error.log",
      out_file: "/home/admin/apps/derrick/Binance-Spot-Trading-Bot/logs/pm2-web-out.log",
      merge_logs: true,

      max_memory_restart: "500M",
    },
  ],
};
```

After changing this file:

```bash
pm2 restart ecosystem.config.js
pm2 save
```

---

# 15. Database and runtime files

The bot should keep runtime data out of Git.

Recommended `.gitignore` entries:

```gitignore
.env
.venv/
data/
outputs/
logs/
.tmp/
__pycache__/
*.pyc
*.pyo
*.sqlite
*.sqlite3
*.db
```

Your SQLite database should live in the project’s `data/` directory unless you intentionally configure another location.

Check the database location:

```bash
ls -al /data
ls -al /home/admin/apps/derrick/Binance-Spot-Trading-Bot/data
```

If the app is creating this file:

```text
/data/trading_bot.db
```

but you expected this:

```text
/home/admin/apps/derrick/Binance-Spot-Trading-Bot/data/trading_bot.db
```

then set the database path explicitly in `.env`, depending on the app’s expected config variable.

A good database path would be:

```env
DATABASE_URL=sqlite:////home/admin/apps/derrick/Binance-Spot-Trading-Bot/data/trading_bot.db
```

Note the four slashes after `sqlite:`. This is required for an absolute SQLite path.

After changing the database path in `.env`:

```bash
pm2 restart trading-bot-web
```

---

# 16. Log management

PM2 logs are written to:

```text
/home/admin/apps/derrick/Binance-Spot-Trading-Bot/logs/pm2-web-out.log
/home/admin/apps/derrick/Binance-Spot-Trading-Bot/logs/pm2-web-error.log
```

View logs:

```bash
pm2 logs trading-bot-web
```

View only the latest lines:

```bash
pm2 logs trading-bot-web --lines 100
```

Clear PM2 logs:

```bash
pm2 flush trading-bot-web
```

Clear all PM2 logs:

```bash
pm2 flush
```

Use this carefully because it removes old PM2 log output.

---

# 17. Troubleshooting

## App shows `online` but dashboard does not load

Check whether port `8880` is listening:

```bash
ss -tulpn | grep 8880
```

Check logs:

```bash
pm2 logs trading-bot-web --lines 100
```

Test locally:

```bash
curl http://127.0.0.1:8880
```

If local works but external access fails, check firewall rules.

## App crashes immediately

Check the error log:

```bash
pm2 logs trading-bot-web --lines 200
```

Common causes:

```text
Missing .env value
Wrong database path
Invalid Binance API key
Missing dependency
Wrong Python interpreter
Port already in use
Permission problem
```

## Port already in use

Check what is using port `8880`:

```bash
sudo lsof -i :8880
```

or:

```bash
ss -tulpn | grep 8880
```

Then either stop the other process or change the bot’s port.

## Wrong Python being used

Check the PM2 config:

```bash
cat ecosystem.config.js
```

The `script` value should point to:

```bash
/home/admin/apps/derrick/Binance-Spot-Trading-Bot/.venv/bin/python
```

Not just:

```bash
python
```

## `.env` permission issue

Fix ownership and permissions:

```bash
cd /home/admin/apps/derrick/Binance-Spot-Trading-Bot
sudo chown admin:sudo .env
chmod 600 .env
```

Restart:

```bash
pm2 restart trading-bot-web
```

---

# 18. Common command cheat sheet

## Pull latest code and restart

```bash
cd /home/admin/apps/derrick/Binance-Spot-Trading-Bot
git pull origin main
source .venv/bin/activate
python -m pip install --upgrade -e ".[web]"
pm2 restart trading-bot-web
pm2 logs trading-bot-web --lines 100
```

## Restart after `.env` change

```bash
cd /home/admin/apps/derrick/Binance-Spot-Trading-Bot
sudo chown admin:sudo .env
chmod 600 .env
pm2 restart trading-bot-web
pm2 logs trading-bot-web --lines 100
```

## Restart after PM2 config change

```bash
cd /home/admin/apps/derrick/Binance-Spot-Trading-Bot
pm2 restart ecosystem.config.js
pm2 save
pm2 logs trading-bot-web --lines 100
```

## Check status

```bash
pm2 status
```

## Check logs

```bash
pm2 logs trading-bot-web
```

## Save PM2 process list

```bash
pm2 save
```

## Stop bot

```bash
pm2 stop trading-bot-web
```

## Start bot again

```bash
pm2 start trading-bot-web
```

---

# 19. Recommended deployment checklist

Use this checklist after any deployment:

```text
[ ] Pulled latest code from GitHub
[ ] Activated .venv
[ ] Reinstalled project dependencies
[ ] Restarted trading-bot-web
[ ] Checked PM2 status
[ ] Checked PM2 logs
[ ] Confirmed dashboard responds locally
[ ] Confirmed .env is not committed to Git
[ ] Confirmed database is stored in the correct data directory
[ ] Ran pm2 save if PM2 config changed
```

---

# 20. One-command deployment script

You can create a helper script so deployments are easier.

Create:

```bash
nano deploy.sh
```

Paste:

```bash
#!/usr/bin/env bash
set -e

APP_DIR="/home/admin/apps/derrick/Binance-Spot-Trading-Bot"
APP_NAME="trading-bot-web"
BRANCH="main"

echo "Moving to app directory..."
cd "$APP_DIR"

echo "Checking Git status..."
git status

echo "Pulling latest code from GitHub..."
git pull origin "$BRANCH"

echo "Activating virtual environment..."
source "$APP_DIR/.venv/bin/activate"

echo "Upgrading installer tools..."
python -m pip install --upgrade pip setuptools wheel

echo "Installing project dependencies..."
python -m pip install --upgrade -e ".[web]"

echo "Ensuring required directories exist..."
mkdir -p "$APP_DIR/logs"
mkdir -p "$APP_DIR/data"
mkdir -p "$APP_DIR/outputs"

echo "Fixing .env permissions if .env exists..."
if [ -f "$APP_DIR/.env" ]; then
  sudo chown admin:sudo "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
fi

echo "Restarting PM2 application..."
pm2 restart "$APP_NAME"

echo "Saving PM2 process list..."
pm2 save

echo "PM2 status:"
pm2 status

echo "Recent logs:"
pm2 logs "$APP_NAME" --lines 80 --nostream

echo "Testing local dashboard..."
curl -I http://127.0.0.1:8880 || true

echo "Deployment complete."
```

Make it executable:

```bash
chmod +x deploy.sh
```

Run it whenever you pull new changes:

```bash
./deploy.sh
```

If your Git branch is not `main`, edit this line in the script:

```bash
BRANCH="main"
```

For example:

```bash
BRANCH="master"
```

---

# 21. Simple mental model

Think of the setup like this:

```text
GitHub code         = latest instructions
.venv              = Python workshop with installed tools
.env               = private settings and secrets
PM2                = supervisor that keeps the bot alive
SQLite data/       = bot memory and trading records
logs/              = what happened while running
```

When code changes, pull from GitHub, refresh the Python workshop, then restart PM2.

When `.env` changes, restart PM2 so the app reads the new settings.

When PM2 config changes, restart using the ecosystem file and run `pm2 save`.

When server reboots, PM2 startup restores the saved process list.
