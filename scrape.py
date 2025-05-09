"""Fetch articles from the Wuthering Waves website and saves them locally in JSON format.

It retrieves the article menu and individual articles, prettifies the JSON output, and sets file timestamps based on article creation dates.
"""  # noqa: CPY001

import asyncio
import json

# Set up logging
import logging
import os
import shutil
import subprocess  # noqa: S404
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, LiteralString

import aiofiles
import httpx

if TYPE_CHECKING:
    from collections.abc import Coroutine

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)

logger: logging.Logger = logging.getLogger("wutheringwaves")


async def fetch_json(url: str, client: httpx.AsyncClient) -> dict[Any, Any] | None:
    """Fetch JSON data from a URL.

    Args:
        url (str): The URL to fetch data from.
        client (httpx.AsyncClient): The HTTP client to use for the request.

    Returns:
        dict[Any, Any] | None: The parsed JSON data if successful, None otherwise.

    """
    try:
        response: httpx.Response = await client.get(url)
        response.raise_for_status()
        return response.json()
    except (httpx.RequestError, json.JSONDecodeError):
        logger.exception("Error fetching %s:", url)
        return None


async def save_prettified_json(data: dict[Any, Any], filepath: Path) -> bool:
    """Save JSON data to a file with pretty formatting.

    Args:
        data (dict[Any, Any]): The JSON data to save.
        filepath (Path): The path to the file where the data will be saved.

    Returns:
        bool: True if the data was saved successfully, False otherwise.

    """
    try:
        async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception:
        logger.exception("Error saving %s:", filepath)
        return False
    else:
        return True


def set_file_timestamp(filepath: Path, timestamp_str: str) -> bool:
    """Set file's modification time based on ISO timestamp string.

    Args:
        filepath (Path): The path to the file.
        timestamp_str (str): The ISO timestamp string.

    Returns:
        bool: True if the timestamp was set successfully, False otherwise.

    """
    try:
        # Parse the timestamp string
        dt: datetime = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)

        # Convert to Unix timestamp
        timestamp: float = dt.timestamp()

        # Set the file's modification time
        os.utime(filepath, (timestamp, timestamp))
    except ValueError:
        logger.info("Error setting timestamp for %s", filepath)
        return False
    else:
        logger.info("Timestamp for %s set to %s", filepath, dt.isoformat())
        return True


def commit_file_with_timestamp(filepath: Path) -> bool:
    """Commit a file to Git with its modification time as the commit time.

    Args:
        filepath (Path): The path to the file to commit.

    Returns:
        bool: True if the commit was successful, False otherwise.

    """
    # Check in Git history if we already have this file
    git_log_cmd: list[str] = ["git", "log", "--pretty=format:%H", "--follow", str(filepath)]
    try:
        git_log_output: str = subprocess.check_output(git_log_cmd, text=True).strip()  # noqa: S603
        if git_log_output:
            logger.info("File %s already exists in Git history.", filepath)
            return True
    except subprocess.CalledProcessError:
        logger.exception("Error checking Git history for %s", filepath)
        return False

    try:
        # Get the full path to the Git executable
        git_executable = shutil.which("git")
        if not git_executable:
            logger.error("Git executable not found.")
            return False

        # Validate the filepath
        if not filepath.is_file():
            logger.error("Invalid file path: %s", filepath)
            return False

        # Get the file's modification time
        timestamp: float = filepath.stat().st_mtime
        git_time: str = datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")

        # Stage the file
        subprocess.run([git_executable, "add", str(filepath)], check=True, text=True)  # noqa: S603

        # Commit the file with the modification time as the commit time
        env: dict[str, str] = {
            **os.environ,
            "GIT_AUTHOR_DATE": git_time,
            "GIT_COMMITTER_DATE": git_time,
        }
        subprocess.run(  # noqa: S603
            [git_executable, "commit", "-m", f"Add {filepath.name}"],
            check=True,
            env=env,
            text=True,
        )
    except subprocess.CalledProcessError:
        logger.exception("Subprocess error occurred while committing the file.")
        return False
    except Exception:
        logger.exception("Error committing %s to Git", filepath)
        return False
    else:
        logger.info("Successfully committed %s to Git", filepath)
        return True


async def main() -> Literal[1, 0]:  # noqa: C901, PLR0912, PLR0914, PLR0915
    """Fetch and save articles from the Wuthering Waves website.

    Returns:
        Literal[1, 0]: 1 if an error occurred, 0 otherwise.

    """
    # Setup
    current_time = int(time.time() * 1000)  # Current time in milliseconds
    base_url = "https://hw-media-cdn-mingchao.kurogame.com/akiwebsite/website2.0/json/G152/en"
    article_menu_url: str = f"{base_url}/ArticleMenu.json?t={current_time}"
    article_base_url: LiteralString = f"{base_url}/article/"
    output_dir = Path("articles")
    output_dir.mkdir(exist_ok=True)

    logger.info("Fetching article menu from %s", article_menu_url)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Fetch the article menu
        menu_data: dict[Any, Any] | None = await fetch_json(article_menu_url, client)
        if not menu_data:
            logger.error("Error: Fetched ArticleMenu.json is empty")
            return 1

        # Save and prettify the menu JSON
        menu_file: Path = output_dir / "ArticleMenu.json"
        if await save_prettified_json(menu_data, menu_file):
            logger.info("Menu JSON saved and prettified to %s", menu_file)

        # Extract article IDs
        logger.info("Extracting article IDs...")
        article_ids: list[str] = [str(item["articleId"]) for item in menu_data if item.get("articleId")]

        if not article_ids:
            logger.warning("No article IDs found. Please check the JSON structure of ArticleMenu.json.")
            logger.warning("Full menu response for debugging:")
            logger.warning(json.dumps(menu_data, indent=2))
            return 1

        # Get list of already downloaded article IDs
        existing_files: list[str] = [file.stem for file in output_dir.glob("*.json") if file.stem != "ArticleMenu"]

        # Filter out already downloaded articles
        article_ids = [article_id for article_id in article_ids if article_id not in existing_files]

        # Download each article
        download_tasks: list[Coroutine[Any, Any, dict[Any, Any] | None]] = []
        for article_id in article_ids:
            article_url: str = f"{article_base_url}{article_id}.json?t={current_time}"
            output_file: Path = output_dir / f"{article_id}.json"

            logger.info("Downloading article %s from %s", article_id, article_url)
            download_tasks.append(fetch_json(article_url, client))

        # Wait for all downloads to complete
        results: list[dict[Any, Any] | BaseException | None] = await asyncio.gather(*download_tasks, return_exceptions=True)

        # Process the downloaded articles
        for i, result in enumerate(results):
            article_id: str = article_ids[i]
            output_file = output_dir / f"{article_id}.json"

            if isinstance(result, Exception):
                logger.error("Error downloading article %s: %s", article_id, result)
                continue

            if not result:
                logger.warning("Downloaded article %s is empty or invalid", article_id)
                continue

            # Save the article JSON
            if isinstance(result, dict) and await save_prettified_json(result, output_file):
                logger.info("Successfully downloaded and prettified %s", output_file)

        json_files: list[Path] = list(output_dir.glob("*.json"))

        # Reverse the JSON files so the youngest articles are at the top of the Git history
        json_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        # Update file timestamps based on createTime
        for file in json_files:
            article_id = file.stem
            if article_id == "ArticleMenu":
                continue

            # Find the corresponding article in the menu
            create_time = None
            for item in menu_data:
                if str(item.get("articleId", "")) == article_id and "createTime" in item:
                    create_time = item["createTime"]
                    break

            if not create_time:
                logger.info("Warning: no createTime for %s in menu data - skipping", article_id)
                continue

            logger.info("Setting %s timestamp to %s", file, create_time)
            if not set_file_timestamp(file, create_time):
                logger.error("  failed to update timestamp")
                continue

            # Commit the file to Git with the correct timestamp
            if not commit_file_with_timestamp(file):
                logger.error("  failed to commit file %s to Git", file)

    logger.info("Script finished. Articles are in the '%s' directory.", output_dir)
    return 0


if __name__ == "__main__":
    asyncio.run(main())
