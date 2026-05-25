/**
 * PM2 Ecosystem Configuration for the Trading Bot.
 */
const path = require("path");

const APP_DIR = "/home/admin/apps/derrick/Binance-Spot-Trading-Bot";

module.exports = {
  apps: [
    {
      name: "trading-bot-web",

      cwd: APP_DIR,

      script: path.join(APP_DIR, ".venv/bin/python"),

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
      error_file: path.join(APP_DIR, "logs/pm2-web-error.log"),
      out_file: path.join(APP_DIR, "logs/pm2-web-out.log"),
      merge_logs: true,

      max_memory_restart: "500M",
    },
  ],
};