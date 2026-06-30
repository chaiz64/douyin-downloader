from core.url_parser import URLParser
from menu import DouyinMenuManager


def test_menu_initialization(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("path: ./Downloaded\nthread: 4\ndatabase: false\nlink:\n  - https://v.douyin.com/xyz\n", encoding="utf-8")

    manager = DouyinMenuManager(config_path=str(config_file))
    assert manager.config.get("thread") == 4
    assert manager.config.get("path") == "./Downloaded"

def test_menu_header_panel():
    manager = DouyinMenuManager()
    header = manager.get_header()
    assert header is not None

def test_live_alias_url_extraction():
    # Test monkey patched alphanumeric live URL extraction
    alias_url_1 = "https://live.douyin.com/20266666ya"
    alias_url_2 = "https://live.douyin.com/hd59188888"
    numeric_url = "https://live.douyin.com/31829834534"

    assert URLParser._extract_room_id(alias_url_1) == "20266666ya"
    assert URLParser._extract_room_id(alias_url_2) == "hd59188888"
    assert URLParser._extract_room_id(numeric_url) == "31829834534"
