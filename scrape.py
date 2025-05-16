import asyncio  # noqa: CPY001, D100
import json
import logging
import os
import re
import shutil
import subprocess  # noqa: S404
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import aiofiles
import httpx
import mdformat
from markdownify import MarkdownConverter  # pyright: ignore[reportMissingTypeStubs]
from markupsafe import Markup, escape

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


def get_file_timestamp(timestamp_str: str) -> float:
    """Convert ISO timestamp string to Unix timestamp.

    Args:
        timestamp_str (str): The ISO timestamp string.

    Returns:
        float: The Unix timestamp, or 0 if conversion failed.

    """
    if not timestamp_str:
        logger.info("Empty timestamp string")
        return 0.0

    try:
        # Parse the timestamp string
        dt: datetime = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        # Convert to Unix timestamp
        return dt.timestamp()
    except ValueError:
        logger.info("Error converting timestamp %s", timestamp_str)
        return 0.0


def commit_file_with_timestamp(filepath: Path) -> bool:  # noqa: PLR0911
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
        git_executable: str | None = shutil.which("git")
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


def add_articles_to_readme(articles: dict[Any, Any] | None = None) -> None:
    """Add the list of articles to the README.md file."""
    if articles is None:
        logger.warning("No articles to add to README.md")
        return

    readme_file: Path = Path("README.md")
    if not readme_file.is_file():
        logger.error("README.md file not found.")
        return

    with readme_file.open("r+", encoding="utf-8") as f:
        # Read existing content
        lines: list[str] = f.readlines()

        # Find "## Articles" section or add it
        articles_section_index = -1
        for i, line in enumerate(lines):
            if line.strip() == "## Articles":
                articles_section_index: int = i
                break

        # Create new content
        new_lines: list[str] = []
        if articles_section_index >= 0:
            new_lines = lines[: articles_section_index + 1]  # Keep everything up to "## Articles"
        else:
            new_lines = lines
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines.append("\n")
            new_lines.append("## Articles\n")

        # Add articles
        new_lines.append("\n")  # Add a blank line after the heading
        for article in sorted(articles, key=lambda x: x.get("createTime", ""), reverse=True):
            article_id: str = str(article.get("articleId", ""))
            article_title: str = article.get("articleTitle", "No Title")
            article_url: str = f"https://wutheringwaves.kurogames.com/en/main/news/detail/{article_id}"
            new_lines.append(
                f"- [{article_title}]({article_url}) [[json]](articles/{article_id}.json)\n",
            )

        # Add articles directory section
        new_lines.append("\n## Articles Directory\n\n")
        new_lines.append("The articles are saved in the `articles` directory.\n")
        new_lines.append("You can view them [here](articles).\n")

        # Write the updated content
        f.seek(0)
        f.truncate()
        f.writelines(new_lines)

        logger.info("Articles added to README.md")


def batch_process_timestamps(menu_data: dict[Any, Any], output_dir: Path) -> None:
    """Process all timestamps in batch for better performance.

    Args:
        menu_data (list[dict[str, Any]]): The article menu data containing timestamps.
        output_dir (Path): Directory containing the article files.

    """
    # Extract article IDs and timestamps
    timestamp_map: dict[str, str] = {}
    for item in menu_data:
        article_id = str(item.get("articleId", ""))
        create_time = item.get("createTime")
        if article_id and create_time:
            timestamp_map[article_id] = create_time

    logger.info("Collected %s timestamps from menu data", len(timestamp_map))

    # Check which files need timestamp updates
    files_to_update: list[tuple[Path, str]] = []
    for article_id, create_time in timestamp_map.items():
        file_path: Path = output_dir / f"{article_id}.json"
        if not file_path.exists():
            continue

        expected_timestamp: float = get_file_timestamp(create_time)
        if expected_timestamp == 0.0:
            continue

        actual_timestamp: float = file_path.stat().st_mtime

        # Only update if timestamps don't match (with a small tolerance)
        if abs(actual_timestamp - expected_timestamp) > 1.0:
            files_to_update.append((file_path, create_time))

    logger.info("Found %s files that need timestamp updates", len(files_to_update))

    # Update timestamps and commit files
    for file_path, create_time in files_to_update:
        logger.info("Setting %s timestamp to %s", file_path, create_time)
        if set_file_timestamp(file_path, create_time):
            if not commit_file_with_timestamp(file_path):
                logger.error("Failed to commit file %s to Git", file_path)
        else:
            logger.error("Failed to update timestamp for %s", file_path)


def format_discord_links(md: str) -> str:
    """Make links work in Discord.

    Discord doesn't support links with titles, so we need to remove them.
    This function also adds angle brackets around the URL to not embed it.

    Args:
        md (str): The Markdown text containing links.

    Returns:
        str: The modified Markdown text with simplified links.

    """

    def repl(match: re.Match[str]) -> str:
        url: str | Any = match.group(2)
        display: str = re.sub(pattern=r"^https?://(www\.)?", repl="", string=url)
        return f"[{display}]({url})"

    # Before: [Link](https://example.com "Link")
    # After: [Link](https://example.com)
    formatted_links_md = re.sub(
        pattern=r'\[([^\]]+)\]\((https?://[^\s)]+) "\2"\)',
        repl=repl,
        string=md,
    )

    # Before: [Link](https://example.com)
    # After: [Link](<https://example.com>)
    add_angle_brackets_md: str = re.sub(
        pattern=r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
        repl=r"[\1](<\2>)",
        string=formatted_links_md,
    )

    return add_angle_brackets_md


def handle_stars(text: str) -> str:
    """Handle stars in the text.

    Args:
        text (str): The text to process.

    Returns:
        str: The processed text with stars replaced by headers.

    """
    lines: list[str] = text.strip().splitlines()
    output: list[str] = []
    for line in lines:
        line: str = line.strip()  # noqa: PLW2901

        # Before: ✦ Title ✦
        # After: # Title
        if line.startswith("✦") and line.endswith("✦"):
            title: str = line.removeprefix("✦").removesuffix("✦").strip()
            output.append(f"# {title}")

        # Before: **✦ Title ✦**
        # After: # Title
        elif line.startswith("**✦") and line.endswith("✦**"):
            title: str = line.removeprefix("**✦").removesuffix("✦**").strip()
            output.append(f"# {title}")

        # Before: ✦ Title
        # After: * Title
        elif line.startswith("✦"):
            title: str = line.removeprefix("✦").strip()
            output.append(f"* {title}")

        elif line:
            output.append(line)
    return "\n\n".join(output)


def generate_atom_feed(articles: list[dict[Any, Any]], file_name: str) -> str:  # noqa: PLR0914
    """Generate an Atom feed from a list of articles.

    Args:
        articles (list[dict[Any, Any]]): The list of articles to include in the feed.
        file_name (str): The name of the file to save the feed to.

    Returns:
        str: The generated Atom feed as a string.

    """
    atom_entries: list[str] = []
    latest_entry: str = datetime.now(UTC).isoformat()

    # Get the latest entry date
    if articles:
        latest_entry = articles[0].get("createTime", "")
        if latest_entry:
            latest_entry = datetime.strptime(str(latest_entry), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).isoformat()

    for article in articles:
        article_id: str = str(article.get("articleId", ""))

        # Use stable identifier based on article ID
        entry_id: str = (
            f"urn:article:{article_id}"
            if article_id
            else f"urn:wutheringwaves:unknown-article-{hash(article.get('articleTitle', '') + article.get('createTime', ''))}"
        )

        article_title: str = article.get("articleTitle", "No Title")
        article_content: str = article.get("articleContent", str(article_title))
        if not article_content:
            article_content = article_title

        converter: MarkdownConverter = MarkdownConverter(
            heading_style="ATX",
            bullets="-",
            strip=["img"],
            default_title="Link",
        )
        article_content_converted = str(converter.convert(article_content).strip())  # type: ignore  # noqa: PGH003

        if not article_content_converted:
            msg: str = f"Article content is empty for article ID: {article_id}"
            logger.warning(msg)
            article_content_converted = "No content available"

        # Remove non-breaking spaces
        xa0_removed: str = re.sub(r"\xa0", " ", article_content_converted)  # Replace non-breaking spaces with regular spaces

        # Replace non-breaking spaces with regular spaces
        non_breaking_space_removed: str = xa0_removed.replace(
            " ",  # noqa: RUF001
            " ",
        )

        # Remove code blocks that has only spaces and newlines inside them
        empty_code_block_removed: str = re.sub(
            pattern=r"```[ \t]*\n[ \t]*\n```",
            repl="",
            string=non_breaking_space_removed,  # type: ignore  # noqa: PGH003
        )

        # [How to Update] should be # How to Update
        square_brackets_converted: str = re.sub(
            pattern=r"^\s*\[([^\]]+)\]\s*$",
            repl=r"# \1",
            string=empty_code_block_removed,  # type: ignore  # noqa: PGH003
            flags=re.MULTILINE,
        )

        stars_converted: str = handle_stars(square_brackets_converted)

        # If `● Word` is in the content, replace it `## Word` instead with regex
        ball_converted: str = re.sub(pattern=r"●\s*(.*?)\n", repl=r"\n\n## \1\n\n", string=stars_converted, flags=re.MULTILINE)

        # If `※ Word` is in the content, replace it `* word * ` instead with regex
        reference_mark_converted: str = re.sub(
            pattern=r"^\s*※\s*(\S.*?)\s*$",
            repl=r"\n\n*\1*\n\n",
            string=ball_converted,
            flags=re.MULTILINE,
        )

        # Replace circled Unicode numbers (①-⑳) with plain numbered text (e.g., "1. ", "2. ", ..., "20. ")
        number_symbol: dict[str, str] = {
            "①": "1",
            "②": "2",
            "③": "3",
            "④": "4",
            "⑤": "5",
            "⑥": "6",
            "⑦": "7",
            "⑧": "8",
            "⑨": "9",
            "⑩": "10",
        }
        for symbol, number in number_symbol.items():
            reference_mark_converted = re.sub(
                pattern=rf"^\s*{re.escape(symbol)}\s*(.*?)\s*$",
                repl=rf"\n\n{number}. \1\n\n",
                string=reference_mark_converted,
                flags=re.MULTILINE,
            )

        space_before_star_added: str = re.sub(pattern=r"\\\*(.*)", repl=r"* \1", string=reference_mark_converted, flags=re.MULTILINE)

        markdown_formatted: str = mdformat.text(  # type: ignore  # noqa: PGH003
            space_before_star_added,
            options={
                "number": True,  # Allow 1., 2., 3. numbering
            },
        )

        links_fixed: str = format_discord_links(markdown_formatted)
        article_escaped: Markup = escape(links_fixed)

        article_url: str = f"https://wutheringwaves.kurogames.com/en/main/news/detail/{article_id}"
        article_create_time: str = article.get("createTime", "")
        published: str = ""
        updated: str = latest_entry

        if article_create_time:
            timestamp: datetime = datetime.strptime(str(article_create_time), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            iso_time: str = timestamp.isoformat()
            published = f"<published>{iso_time}</published>"
            updated = iso_time

        article_category: str = article.get("articleTypeName", "Wuthering Waves")
        category: str = f'<category term="{escape(article_category)}"/>' if article_category else ""
        atom_entries.append(
            f"""
    <entry>
        <id>{entry_id}</id>
        <title>{escape(article_title)}</title>
        <link href="{article_url}" rel="alternate" type="text/html"/>
        <content type="text">{article_escaped}</content>
        {published}
        <updated>{updated}</updated>
        {category}
        <author>
            <name>Wuthering Waves</name>
            <email>wutheringwaves_ensupport@kurogames.com</email>
            <uri>https://wutheringwaves.kurogames.com</uri>
        </author>
    </entry>""",
        )

    # Create the complete Atom feed
    atom_feed: str = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
    <title>Wuthering Waves Articles</title>
    <link href="https://wutheringwaves.kurogames.com/en/main/news/" rel="alternate" type="text/html"/>
    <link href="https://git.lovinator.space/TheLovinator/wutheringwaves/raw/branch/master/{file_name}" rel="self" type="application/atom+xml"/>
    <id>urn:wutheringwaves:feed</id>
    <updated>{latest_entry}</updated>
    <subtitle>Latest articles from Wuthering Waves</subtitle>
    <icon>https://git.lovinator.space/TheLovinator/wutheringwaves/raw/branch/master/logo.png</icon>
    <logo>https://git.lovinator.space/TheLovinator/wutheringwaves/raw/branch/master/logo.png</logo>
    <rights>Copyright © {datetime.now(tz=UTC).year} Wuthering Waves</rights>
    <generator uri="https://git.lovinator.space/TheLovinator/wutheringwaves" version="1.0">Python Script</generator>
    <author>
        <name>Wuthering Waves</name>
        <email>wutheringwaves_ensupport@kurogames.com</email>
        <uri>https://wutheringwaves.kurogames.com</uri>
    </author>
    {"".join(atom_entries)}
</feed>"""  # noqa: E501

    return atom_feed


def create_atom_feeds(output_dir: Path) -> None:
    """Create Atom feeds for the articles.

    Current feeds are:
        - Last 10 articles
        - All articles

    Args:
        output_dir (Path): The directory to save the RSS feed files.

    """
    menu_data: list[dict[Any, Any]] = []
    # Load data from all the articles
    for file in output_dir.glob("*.json"):
        if file.stem == "ArticleMenu":
            continue
        with file.open("r", encoding="utf-8") as f:
            try:
                article_data: dict[Any, Any] = json.load(f)
                menu_data.append(article_data)
            except json.JSONDecodeError:
                logger.exception("Error decoding JSON from %s", file)
                continue

    if not menu_data:
        logger.error("Can't create Atom feeds, no articles found in %s", output_dir)
        return

    articles_sorted: list[dict[Any, Any]] = sorted(
        menu_data,
        key=lambda x: get_file_timestamp(x.get("createTime", "")),
        reverse=True,
    )

    # Create the Atom feed for the latest articles
    amount_of_articles: int = 20
    atom_feed_path: Path = Path("articles_latest.xml")
    latest_articles: list[dict[Any, Any]] = articles_sorted[:amount_of_articles]

    logger.info("Dates of the last %s articles:", len(latest_articles))
    for article in latest_articles:
        article_id: str = str(article.get("articleId", ""))
        article_create_time: str = article.get("createTime", "")
        logger.info("\tArticle ID: %s, Date: %s", article_id, article_create_time)

    atom_feed: str = generate_atom_feed(articles=latest_articles, file_name=atom_feed_path.name)
    with atom_feed_path.open("w", encoding="utf-8") as f:
        f.write(atom_feed)
    logger.info("Created Atom feed for the last %s articles: %s", len(latest_articles), atom_feed_path)

    # Create the Atom feed for all articles
    atom_feed_path_all: Path = Path("articles_all.xml")
    atom_feed_all_articles: str = generate_atom_feed(articles=articles_sorted, file_name=atom_feed_path_all.name)
    with atom_feed_path_all.open("w", encoding="utf-8") as f:
        f.write(atom_feed_all_articles)
    logger.info("Created Atom feed for all articles: %s", atom_feed_path_all)


def add_data_to_articles(menu_data: dict[Any, Any], output_dir: Path) -> None:
    """ArticleMenu.json contains data that should be added to the articles.

    Fields not in the article JSON:
        - articleDesc (Currently empty in ArticleMenu.json)
        - createTime
        - sortingMark
        - suggestCover
        - top

    Args:
        menu_data (dict[Any, Any]): The article menu data.
        output_dir (Path): Directory containing the article files.

    """
    for item in menu_data:
        article_id: str = str(item.get("articleId", ""))
        if not article_id:
            continue

        # Check if the article file exists
        article_file: Path = output_dir / f"{article_id}.json"
        if not article_file.is_file():
            logger.warning("Article file %s does not exist, skipping...", article_file)
            continue

        # Read the existing article data
        with article_file.open("r", encoding="utf-8") as f:
            try:
                article_data: dict[Any, Any] = json.load(f)
            except json.JSONDecodeError:
                logger.exception("Error decoding JSON from %s", article_file)
                continue

        old_article_data = article_data

        # Add missing fields from ArticleMenu.json
        for key in ["articleDesc", "createTime", "sortingMark", "suggestCover", "top"]:
            if key in item and key not in article_data:
                article_data[key] = item[key]

        # Save the updated article data if any changes were made
        if old_article_data != article_data:
            with article_file.open("w", encoding="utf-8") as f:
                json.dump(article_data, f, indent=2, ensure_ascii=False)
            logger.info("Updated %s with data from ArticleMenu.json", article_file)


async def main() -> Literal[1, 0]:
    """Fetch and save articles from the Wuthering Waves website.

    Returns:
        Literal[1, 0]: 1 if an error occurred, 0 otherwise.

    """
    # Setup
    current_time = int(time.time() * 1000)  # Current time in milliseconds
    base_url = "https://hw-media-cdn-mingchao.kurogame.com/akiwebsite/website2.0/json/G152/en"
    article_menu_url: str = f"{base_url}/ArticleMenu.json?t={current_time}"
    article_base_url: str = f"{base_url}/article/"
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
        new_article_ids: list[str] = [article_id for article_id in article_ids if article_id not in existing_files]

        if new_article_ids:
            logger.info("Found %s new articles to download", len(new_article_ids))

            # Download each new article
            download_tasks: list[Coroutine[Any, Any, dict[Any, Any] | None]] = []
            for article_id in new_article_ids:
                article_url: str = f"{article_base_url}{article_id}.json?t={current_time}"
                output_file: Path = output_dir / f"{article_id}.json"

                logger.info("Downloading article %s from %s", article_id, article_url)
                download_tasks.append(fetch_json(article_url, client))

            # Wait for all downloads to complete
            results: list[dict[Any, Any] | BaseException | None] = await asyncio.gather(*download_tasks, return_exceptions=True)

            # Process the downloaded articles
            for i, result in enumerate(results):
                article_id: str = new_article_ids[i]
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
        else:
            logger.info("No new articles to download")

    add_data_to_articles(menu_data, output_dir)
    add_articles_to_readme(menu_data)
    create_atom_feeds(output_dir)
    batch_process_timestamps(menu_data, output_dir)

    logger.info("Script finished. Articles are in the '%s' directory.", output_dir)
    return 0


if __name__ == "__main__":
    asyncio.run(main())
