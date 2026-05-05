import os
from pathlib import Path


bind = f"{os.getenv('LISTEN_HOST', '0.0.0.0')}:{os.getenv('LISTEN_PORT', '9081')}"
# Keep a single worker because the bot starts in-process polling and dispatch threads.
workers = 1
timeout = 120
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
capture_output = True


def post_worker_init(worker):
    import issue_bot_service

    env_file = Path(os.getenv("CODING_BOT_ENV_FILE", Path(__file__).with_name(".env"))).expanduser()
    issue_bot_service.bootstrap_service(env_file.resolve(strict=False))
