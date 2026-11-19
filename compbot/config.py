"""Loads config.toml from the project folder."""
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
DATA_DIR = ROOT / "data"


@dataclass
class Config:
    discord_webhook: str
    gmail_address: str
    gmail_app_password: str
    me: dict
    gmail_relay_url: str = ""
    unknown_senders: str = "quiet"
    feed_url: str = "https://www.ozbargain.com.au/competition/feed"
    feed_minutes: int = 20
    inbox_minutes: int = 10
    inbox_lookback_days: int = 14
    exclude_categories: list = field(default_factory=list)
    exclude_keywords: list = field(default_factory=list)
    exclude_gleam: bool = True
    exclude_instagram: bool = True
    win_keywords: list = field(default_factory=list)
    browser_channel: str = ""
    browser_mode: str = "normal"
    chrome_profile: str = ""
    close_tabs: bool = True


DEFAULT_WIN_KEYWORDS = [
    "congratulations", "congrats", "you've won", "you have won", "you won",
    "winner", "lucky winner", "prize", "claim your", "you're a winner",
    "selected as", "drawn as",
]


def load(path: Path = CONFIG_PATH) -> Config:
    if path.exists():
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    elif os.environ.get("COMPBOT_DISCORD_WEBHOOK"):
        raw = {}  # settings come from COMPBOT_* environment variables
    else:
        raise SystemExit(
            f"Missing {path.name}. Copy config.example.toml to config.toml and fill it in."
        )
    env = os.environ.get
    raw.setdefault("discord", {}).setdefault("webhook_url", env("COMPBOT_DISCORD_WEBHOOK", ""))
    raw.setdefault("gmail", {}).setdefault("address", env("COMPBOT_GMAIL_ADDRESS", ""))
    raw["gmail"].setdefault("app_password", env("COMPBOT_GMAIL_APP_PASSWORD", ""))
    discord = raw.get("discord", {})
    gmail = raw.get("gmail", {})
    feed = raw.get("feed", {})
    inbox = raw.get("inbox", {})
    browser = raw.get("browser", {})
    return Config(
        discord_webhook=discord.get("webhook_url", ""),
        gmail_address=gmail.get("address", ""),
        gmail_app_password=gmail.get("app_password", "").replace(" ", ""),
        gmail_relay_url=gmail.get("relay_url", "").strip(),
        unknown_senders=str(inbox.get("unknown_senders", "quiet")).lower(),
        me=raw.get("me", {}),
        feed_url=feed.get("url", Config.feed_url),
        feed_minutes=int(feed.get("check_every_minutes", 20)),
        inbox_minutes=int(inbox.get("check_every_minutes", 10)),
        inbox_lookback_days=int(inbox.get("first_run_lookback_days", 14)),
        exclude_categories=[c.lower() for c in feed.get("exclude_categories", [])],
        exclude_keywords=[k.lower() for k in feed.get("exclude_keywords", [])],
        exclude_gleam=bool(feed.get("exclude_gleam", True)),
        exclude_instagram=bool(feed.get("exclude_instagram", True)),
        win_keywords=[k.lower() for k in inbox.get("win_keywords", DEFAULT_WIN_KEYWORDS)],
        browser_channel=browser.get("channel", ""),
        browser_mode=str(browser.get("mode", "normal")).lower(),
        chrome_profile=str(browser.get("chrome_profile", "")).strip(),
        close_tabs=bool(browser.get("close_tabs", True)),
    )
