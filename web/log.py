import os
from datetime import datetime, timedelta

LAST_CLEANUP_DAY = "2000-02-29"
CLEANUP_HOUR_UTC = 4


def run_daily_cleanup_if_needed(log_dir: str, logger) -> None:
    global LAST_CLEANUP_DAY

    current_time = datetime.utcnow()
    today_str = current_time.strftime("%Y-%m-%d")

    if today_str <= LAST_CLEANUP_DAY or current_time.hour < CLEANUP_HOUR_UTC:
        return

    logger.info(f"Triggering daily log cleanup for {today_str}.")
    try:
        cutoff_date = current_time - timedelta(days=7)

        if not os.path.isdir(log_dir):
            logger.warning(f"Log cleanup skipped: Directory '{log_dir}' not found.")
            LAST_CLEANUP_DAY = today_str
            return

        for filename in os.listdir(log_dir):
            if not filename.endswith(".txt"):
                continue

            try:
                file_path = os.path.join(log_dir, filename)
                file_date_str = os.path.splitext(filename)[0]
                file_date = datetime.strptime(file_date_str, "%Y-%m-%d")
                if file_date < cutoff_date:
                    os.remove(file_path)
            except (ValueError, IndexError):
                logger.debug(f"Log cleanup skipped invalid filename: {filename}")
                continue

        LAST_CLEANUP_DAY = today_str
        logger.info(f"Daily log cleanup for {today_str} completed.")
    except Exception as exc:
        logger.error(f"An error occurred during log cleanup: {exc}")
