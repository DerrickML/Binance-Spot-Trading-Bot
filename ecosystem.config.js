/**
 * PM2 Ecosystem Configuration for the Trading Bot.
 *
 * This config:
 * - Runs the app using the project's virtualenv Python
 * - Sets the working directory
 * - Loads variables from .env into PM2
 * - Starts the FastAPI web dashboard on port 8880
 */

const fs = require("fs");
const path = require("path");

const APP_DIR = "/home/admin/apps/derrick/Binance-Spot-Trading-Bot";
const ENV_FILE = path.join(APP_DIR, ".env");

function loadEnvFile(filePath) {
  const env = {};

  if (!fs.existsSync(filePath)) {
    return env;
  }

  const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/);

  for (const line of lines) {
    const trimmed = line.trim();

    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }

    const equalsIndex = trimmed.indexOf("=");

    if (equalsIndex === -1) {
      continue;
    }

    const key = trimmed.slice(0, equalsIndex).trim();
    let value = trimmed.slice(equalsIndex + 1).trim();

    if (!key) {
      continue;
    }

    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }

    env[key] = value;
  }

  return env;
}

const fileEnv = loadEnvFile(ENV_FILE);

module.exports = {
  apps: [
    {
      name: "trading-bot-web",

      cwd: APP_DIR,

      script: path.join(APP_DIR, ".venv/bin/python"),

      args: "-m uvicorn app.web.server:app --host 0.0.0.0 --port 8880",

      interpreter: "none",

      env: {
        ...fileEnv,

        PYTHONUNBUFFERED: "1",

        WEB_PORT: fileEnv.WEB_PORT || "8880",
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