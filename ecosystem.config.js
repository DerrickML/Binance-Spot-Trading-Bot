/**
 * PM2 Ecosystem Configuration for the Trading Bot.
 *
 * Usage:
 *   pm2 start ecosystem.config.js
 *   pm2 status
 *   pm2 logs trading-bot-web
 *   pm2 stop all
 *
 * The web dashboard runs on port 8880 by default.
 * Paper trading and other CLI commands are launched from the dashboard UI.
 */
module.exports = {
  apps: [
    {
      name: "trading-bot-web",
      script: "python",
      args: "-m uvicorn app.web.server:app --host 0.0.0.0 --port 8880",
      interpreter: "none",
      env: {
        PYTHONUNBUFFERED: "1",
        WEB_PORT: "8880",
      },
      // Restart policy
      max_restarts: 10,
      restart_delay: 5000,
      autorestart: true,

      // Logging
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
      error_file: "logs/pm2-web-error.log",
      out_file: "logs/pm2-web-out.log",
      merge_logs: true,

      // Resource limits
      max_memory_restart: "500M",
    },
  ],
};
