#!/usr/bin/env python3
import asyncio
import json
import os
import platform
import re
import shutil
import sys
from pathlib import Path
from typing import List, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

# Bootstrap path so menu.py runs seamlessly from anywhere
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

from auth import CookieManager  # noqa: E402
from config import ConfigLoader  # noqa: E402
from core.api_client import DouyinAPIClient  # noqa: E402
from core.url_parser import URLParser  # noqa: E402
from storage import Database  # noqa: E402

console = Console()


# --- LIVE STREAM ALIAS RESOLUTION & DOMAIN MONKEY PATCH ---

# 1. Monkey patch URLParser._extract_room_id to support alphanumeric room aliases
def patched_extract_room_id(url: str) -> str:
    match = re.search(r"/live/([A-Za-z0-9_-]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"live\.douyin\.com/([A-Za-z0-9_-]+)", url)
    if match:
        return match.group(1)
    return ""


URLParser._extract_room_id = staticmethod(patched_extract_room_id)

# 2. Monkey patch DouyinAPIClient._request_json to route webcast calls through live.douyin.com domain
original_request_json = DouyinAPIClient._request_json


async def patched_request_json(self, path, params, *args, **kwargs):
    if path == "/webcast/room/web/enter/":
        old_url = self.BASE_URL
        self.BASE_URL = "https://live.douyin.com"
        try:
            return await original_request_json(self, path, params, *args, **kwargs)
        finally:
            self.BASE_URL = old_url
    return await original_request_json(self, path, params, *args, **kwargs)


DouyinAPIClient._request_json = patched_request_json


# 3. Add helper to resolve alphanumeric live aliases to real numeric roomIds
async def resolve_real_room_id(api_client: DouyinAPIClient, alias_or_id: str) -> str:
    """If the alias contains letters, fetch the live page HTML to extract the real numeric roomId."""
    if alias_or_id.isdigit():
        return alias_or_id

    url = f"https://live.douyin.com/{alias_or_id}"
    try:
        session = await api_client.get_session()
        async with session.get(
            url, headers=api_client.headers, proxy=api_client.proxy or None
        ) as resp:
            html = await resp.text()
            patterns = [
                r'"roomId"\s*:\s*"(\d+)"',
                r'"id_str"\s*:\s*"(\d+)"',
                r'"room_id"\s*:\s*"(\d+)"',
                r'"idStr"\s*:\s*"(\d+)"',
                r'\\"roomId\\"\s*:\s*\\"(\d+)\\"',
                r'\\"id_str\\"\s*:\s*\\"(\d+)\\"',
                r'"room"\s*:\s*\{\s*"id"\s*:\s*(\d+)',
                r'"roomId"\s*:\s*(\d+)',
            ]
            for pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    return match.group(1)
    except Exception:
        pass
    return alias_or_id


def get_key() -> str:
    """Reads a single keypress from the terminal, supporting arrow keys across platforms."""
    if os.name == "nt":
        import msvcrt

        ch = msvcrt.getch()
        if ch in (b"\x00", b"\xe0"):
            ch2 = msvcrt.getch()
            if ch2 == b"H":
                return "up"
            elif ch2 == b"P":
                return "down"
            elif ch2 == b"K":
                return "left"
            elif ch2 == b"M":
                return "right"
        elif ch == b"\r":
            return "enter"
        elif ch == b"\x1b":
            return "escape"
        elif ch == b"\x03":
            raise KeyboardInterrupt()
        try:
            return ch.decode("utf-8", errors="ignore")
        except Exception:
            return ""
    else:
        import termios
        import tty

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                ch2 = sys.stdin.read(1)
                if ch2 == "[":
                    ch3 = sys.stdin.read(1)
                    if ch3 == "A":
                        return "up"
                    elif ch3 == "B":
                        return "down"
                    elif ch3 == "C":
                        return "right"
                    elif ch3 == "D":
                        return "left"
                return "escape"
            elif ch in ("\r", "\n"):
                return "enter"
            elif ch == "\x03":
                raise KeyboardInterrupt()
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


class DouyinMenuManager:
    def __init__(self, config_path: str = "config.yml"):
        self.config_path = config_path
        self.load_config()

    def load_config(self):
        if Path(self.config_path).exists():
            try:
                self.config = ConfigLoader(self.config_path)
            except Exception:
                self.config = ConfigLoader(None)
        else:
            self.config = ConfigLoader(None)

        self.cookie_manager = CookieManager()
        self.cookie_manager.set_cookies(self.config.get_cookies())

    def get_header(self) -> Panel:
        """Generates the rich header panel with system & status information."""
        cookies = self.cookie_manager.get_cookies()
        has_cookies = bool(cookies and cookies.get("msToken"))
        cookie_status = (
            "[bold green]Active[/bold green]"
            if has_cookies
            else "[bold red]Missing / Incomplete[/bold red]"
        )

        save_path = self.config.get("path") or "./Downloaded/"
        thread_count = self.config.get("thread", 5)
        db_enabled = (
            "[green]Enabled[/green]" if self.config.get("database") else "[dim]Disabled[/dim]"
        )

        system_info = f"[cyan]{platform.system()} ({platform.machine()})[/cyan]"
        python_ver = f"[yellow]Py {platform.python_version()}[/yellow]"

        grid = Table.grid(expand=True)
        grid.add_column(justify="left")
        grid.add_column(justify="right")

        title_text = Text("🎵 DOUYIN DOWNLOADER MANAGER 🎵", style="bold magenta launcher")
        sub_text = Text(
            "Standalone Interactive Command Center & Batch Suite", style="italic dim white"
        )

        left_side = Text()
        left_side.append("🔑 Session Status: ", style="bold")
        left_side.append_text(Text.from_markup(cookie_status))
        left_side.append("\n📁 Save Path: ", style="bold")
        left_side.append(str(save_path), style="cyan")

        right_side = Text()
        right_side.append("⚡ Threads: ", style="bold")
        right_side.append(str(thread_count), style="yellow")
        right_side.append(" | DB: ", style="bold")
        right_side.append_text(Text.from_markup(db_enabled))
        right_side.append("\n💻 Sys: ", style="bold")
        right_side.append_text(Text.from_markup(f"{system_info} | {python_ver}"))

        grid.add_row(left_side, right_side)

        header_content = Table.grid(expand=True)
        header_content.add_column(justify="center")
        header_content.add_row(title_text)
        header_content.add_row(sub_text)
        header_content.add_row("")
        header_content.add_row(grid)

        return Panel(
            header_content,
            style="bright_blue",
            border_style="bold cyan",
            padding=(0, 2),
        )

    def select_menu(self, options: List[Tuple[str, str, str]], title: str = "Main Menu") -> int:
        """Interactive selection menu using arrow keys."""
        selected_index = 0

        while True:
            console.clear()
            console.print(self.get_header())
            console.print(
                f"\n[bold gold1]❓ {title}:[/bold gold1] [dim](Use ↑/↓ arrow keys to select, Enter to confirm)[/dim]\n"
            )

            menu_table = Table(show_header=False, box=None, padding=(0, 1), expand=True)
            menu_table.add_column(width=4, justify="center")
            menu_table.add_column(width=35, justify="left")
            menu_table.add_column(justify="left")

            for idx, (icon, label, desc) in enumerate(options):
                if idx == selected_index:
                    cursor = "[bold green]➔[/bold green]"
                    item_style = (
                        "[bold cyan reverse] " + icon + " " + label + " [/bold cyan reverse]"
                    )
                    desc_style = f"[bold white]{desc}[/bold white]"
                else:
                    cursor = " "
                    item_style = f"[dim]{icon} {label}[/dim]"
                    desc_style = f"[dim]{desc}[/dim]"

                menu_table.add_row(cursor, item_style, desc_style)

            console.print(menu_table)
            console.print("\n[dim]Press Ctrl+C or select Exit to quit.[/dim]")

            try:
                key = get_key()
                if key == "up":
                    selected_index = (selected_index - 1) % len(options)
                elif key == "down":
                    selected_index = (selected_index + 1) % len(options)
                elif key == "enter":
                    return selected_index
                elif key == "escape":
                    return len(options) - 1
            except KeyboardInterrupt:
                return len(options) - 1

    async def start(self):
        """Main loop for the TUI."""
        # Auto-run wizard if config.yml does not exist
        if not Path(self.config_path).exists():
            console.print(
                "[bold yellow]⚠️ No config.yml detected. Launching first-time setup wizard...[/bold yellow]\n"
            )
            await self.handle_first_time_setup()

        main_options = [
            (
                "⚡",
                "Quick Download (URL/Shortlink)",
                "Download single or multiple URLs interactively",
            ),
            (
                "🔴",
                "Record Live Stream (experimental)",
                "Record live stream videos from live.douyin.com",
            ),
            (
                "📋",
                "Batch Process (Config Links)",
                "Run batch downloader using links from config.yml",
            ),
            ("🔥", "Discovery & Search Tools", "Explore Douyin Hot Search board or search by keyword"),
            ("🔑", "Session & Cookie Setup", "Manage login state, paste cookies, or run browser login"),
            ("⚙️", "App Settings & Paths", "Modify output path, threads, rate limits & DB settings"),
            (
                "🛠️",
                "Database Explorer & History",
                "Search and view recorded download history with pagination",
            ),
            ("🩺", "Self-Diagnostic Test", "Run complete diagnostic check on cookies, DB, & packages"),
            ("🪄", "Run First-Time Setup Wizard", "Reset configuration and walk through setup wizard"),
            ("🚪", "Exit", "Close Douyin Downloader Manager"),
        ]

        while True:
            choice = self.select_menu(main_options, title="Douyin Downloader Main Menu")

            try:
                if choice == 0:
                    await self.handle_quick_download()
                elif choice == 1:
                    await self.handle_live_stream()
                elif choice == 2:
                    await self.handle_batch_process()
                elif choice == 3:
                    await self.handle_discovery()
                elif choice == 4:
                    await self.handle_cookie_setup()
                elif choice == 5:
                    await self.handle_settings()
                elif choice == 6:
                    await self.handle_history_paging()
                elif choice == 7:
                    await self.handle_diagnostics()
                elif choice == 8:
                    await self.handle_first_time_setup()
                elif choice == 9:
                    console.print(
                        "\n[bold yellow]👋 Exiting Douyin Downloader Manager. Goodbye![/bold yellow]\n"
                    )
                    break
            except KeyboardInterrupt:
                console.print(
                    "\n[bold yellow]⚠️ Action cancelled. Returning to main menu...[/bold yellow]"
                )
                await asyncio.sleep(1)

    async def handle_first_time_setup(self):
        console.clear()
        console.print(
            Panel(
                "[bold magenta]🪄 FIRST-TIME SETUP WIZARD[/bold magenta]\n\n"
                "This wizard will help you initialize Douyin Downloader config step-by-step.",
                style="bold magenta",
            )
        )

        try:
            # Step 1: Create config.yml from example if needed
            if not Path(self.config_path).exists():
                example_path = Path("config.example.yml")
                if example_path.exists():
                    shutil.copy(example_path, self.config_path)
                    console.print("[green]✓ Created config.yml from template example.[/green]")
                else:
                    Path(self.config_path).write_text("path: ./Downloaded/\nthread: 5\n", encoding="utf-8")
                    console.print("[green]✓ Initialized base config.yml file.[/green]")
            self.load_config()

            # Step 2: Set save path
            default_path = self.config.get("path") or "./Downloaded/"
            new_path = Prompt.ask(
                "[bold cyan]Step 1/3: Save folder path[/bold cyan]", default=str(default_path)
            ).strip()
            self.config.update(path=new_path)

            # Step 3: Set concurrent threads
            default_threads = self.config.get("thread", 5)
            new_threads = Prompt.ask(
                "[bold cyan]Step 2/3: Concurrent threads[/bold cyan]", default=str(default_threads)
            ).strip()
            self.config.update(thread=int(new_threads) if new_threads.isdigit() else default_threads)

            # Step 4: Toggle database history
            enable_db = Confirm.ask(
                "[bold cyan]Step 3/3: Enable SQLite history database & deduplication?[/bold cyan]",
                default=True,
            )
            self.config.update(database=enable_db)

            self.config.save()
            self.load_config()
            console.print(
                "\n[bold green]✓ Configuration completed and saved successfully![/bold green]\n"
            )

            # Prompt to do cookie fetcher
            if Confirm.ask("Would you like to run the interactive login tool to fetch cookies now?"):
                await self.handle_run_cookie_fetcher()
        except KeyboardInterrupt:
            console.print("\n[bold red]⚠️ Setup wizard skipped by user.[/bold red]")
            await asyncio.sleep(1)
            return

        Prompt.ask("\n[bold cyan]Press Enter to return to main menu...[/bold cyan]")

    async def handle_run_cookie_fetcher(self) -> bool:
        try:
            from tools.cookie_fetcher import fetch_cookies

            console.print(
                "[info]Launching browser for login... Please complete login in the opened window.[/info]"
            )
            output_path = Path("config/cookies.json")
            config_p = Path(self.config_path)
            code = await fetch_cookies(output=output_path, config=config_p)
            if code == 0:
                console.print("[bold green]✓ Cookies successfully refreshed & saved![/bold green]")
                self.load_config()
                return True
        except ImportError:
            console.print(
                "[bold red]Playwright is not installed. Run 'pip install playwright' and 'playwright install' first.[/bold red]"
            )
        except Exception as e:
            console.print(f"[bold red]Error launching browser tool: {e}[/bold red]")
        return False

    async def handle_quick_download(self):
        console.clear()
        console.print(self.get_header())
        console.print("\n[bold cyan]⚡ Quick Download Scenario[/bold cyan]")
        console.print(
            "[dim]Paste one or more Douyin video/user links below (separated by spaces or commas).[/dim]\n"
        )

        raw_input = Prompt.ask("[bold green]Enter Douyin URL(s)[/bold green]").strip()
        if not raw_input:
            return

        urls = [u.strip() for u in raw_input.replace(",", " ").split() if u.strip()]

        from cli.main import ProgressDisplay, _run_with_relogin, download_url

        display = ProgressDisplay()
        database = None
        if self.config.get("database"):
            db_path = self.config.get("database_path", "dy_downloader.db") or "dy_downloader.db"
            database = Database(db_path=str(db_path))
            await database.initialize()

        console.print(
            f"\n[bold green]🚀 Starting download for {len(urls)} link(s)...[/bold green]\n"
        )
        display.start_download_session(len(urls))

        try:
            for i, url in enumerate(urls, 1):
                display.start_url(i, len(urls), url)
                result = await _run_with_relogin(
                    lambda u=url: download_url(
                        u, self.config, self.cookie_manager, database, progress_reporter=display
                    ),
                    self.cookie_manager,
                    serve=False,
                )
                if result:
                    display.complete_url(result)
                else:
                    display.fail_url("Download failed or invalid URL")
        finally:
            display.stop_download_session()
            if database:
                await database.close()

        Prompt.ask("\n[bold cyan]Press Enter to return to main menu...[/bold cyan]")

    async def handle_live_stream(self):
        console.clear()
        console.print(self.get_header())
        console.print("\n[bold red]🔴 Record Live Stream Scenario (experimental)[/bold red]")
        console.print(
            "[dim]Enter Douyin Live room URL or Room ID (e.g., https://live.douyin.com/123456789 or 123456789).[/dim]\n"
        )

        raw_input = Prompt.ask("[bold green]Enter Live Stream URL or Room ID[/bold green]").strip()
        if not raw_input:
            return

        live_url = raw_input
        if not live_url.startswith("http"):
            live_url = f"https://live.douyin.com/{live_url}"

        # Resolve alphanumeric room ID alias to numeric room ID beforehand
        raw_room_id = URLParser.parse(live_url).get("room_id") if URLParser.parse(live_url) else None
        if raw_room_id:
            async with DouyinAPIClient(self.cookie_manager.get_cookies()) as client:
                resolved_id = await resolve_real_room_id(client, raw_room_id)
                live_url = f"https://live.douyin.com/{resolved_id}"

        live_cfg = self.config.get("live") or {}
        cur_max_dur = live_cfg.get("max_duration_seconds", 0)
        console.print(
            f"Current max recording duration: [yellow]{cur_max_dur} seconds[/yellow] (0 = until stream ends)"
        )

        if Confirm.ask("Would you like to change max duration for this session?"):
            dur_str = Prompt.ask(
                "Enter max duration in seconds (0 for unlimited)", default=str(cur_max_dur)
            )
            if dur_str.isdigit():
                if "live" not in self.config.config:
                    self.config.config["live"] = {}
                self.config.config["live"]["max_duration_seconds"] = int(dur_str)

        from cli.main import ProgressDisplay, _run_with_relogin, download_url

        display = ProgressDisplay()
        database = None
        if self.config.get("database"):
            db_path = self.config.get("database_path", "dy_downloader.db") or "dy_downloader.db"
            database = Database(db_path=str(db_path))
            await database.initialize()

        console.print(f"\n[bold red]🔴 Connecting to live stream: {live_url}...[/bold red]\n")
        display.start_download_session(1)

        try:
            display.start_url(1, 1, live_url)
            result = await _run_with_relogin(
                lambda u=live_url: download_url(
                    u, self.config, self.cookie_manager, database, progress_reporter=display
                ),
                self.cookie_manager,
                serve=False,
            )
            if result:
                display.complete_url(result)
            else:
                display.fail_url("Live recording ended or failed")
        finally:
            display.stop_download_session()
            if database:
                await database.close()

        Prompt.ask("\n[bold cyan]Press Enter to return to main menu...[/bold cyan]")

    async def handle_batch_process(self):
        console.clear()
        console.print(self.get_header())
        console.print("\n[bold cyan]📋 Batch Process Scenario[/bold cyan]\n")

        links = self.config.get_links()
        console.print(
            f"Found [bold yellow]{len(links)}[/bold yellow] link(s) configured in [cyan]{self.config_path}[/cyan]."
        )

        if not links:
            console.print("[yellow]No links found in configuration file.[/yellow]")
            custom_file = Prompt.ask(
                "[bold green]Path to links text file (or Enter to skip)[/bold green]", default=""
            ).strip()
            if custom_file and Path(custom_file).exists():
                lines = Path(custom_file).read_text(encoding="utf-8").splitlines()
                links = [
                    line.strip()
                    for line in lines
                    if line.strip() and not line.strip().startswith("#")
                ]
                console.print(
                    f"Loaded [bold yellow]{len(links)}[/bold yellow] link(s) from [cyan]{custom_file}[/cyan]."
                )
            else:
                return

        if not Confirm.ask(f"Do you want to start downloading these {len(links)} items now?"):
            return

        from cli.main import ProgressDisplay, _run_with_relogin, download_url

        display = ProgressDisplay()
        database = None
        if self.config.get("database"):
            db_path = self.config.get("database_path", "dy_downloader.db") or "dy_downloader.db"
            database = Database(db_path=str(db_path))
            await database.initialize()

        display.start_download_session(len(links))
        try:
            for i, url in enumerate(links, 1):
                display.start_url(i, len(links), url)
                result = await _run_with_relogin(
                    lambda u=url: download_url(
                        u, self.config, self.cookie_manager, database, progress_reporter=display
                    ),
                    self.cookie_manager,
                    serve=False,
                )
                if result:
                    display.complete_url(result)
                else:
                    display.fail_url("Download failed")
        finally:
            display.stop_download_session()
            if database:
                await database.close()

        Prompt.ask("\n[bold cyan]Press Enter to return to main menu...[/bold cyan]")

    async def handle_discovery(self):
        disc_options = [
            ("🔥", "Fetch Hot Search Board (抖音热搜榜)", "Dump current trending search board items"),
            ("🔍", "Search Works by Keyword", "Search videos/galleries by specified keyword"),
            ("🔙", "Back to Main Menu", "Return to main menu"),
        ]
        choice = self.select_menu(disc_options, title="Discovery & Search Submenu")

        if choice == 0:
            console.clear()
            console.print(self.get_header())
            console.print("\n[bold cyan]🔥 Fetch Hot Search Board[/bold cyan]\n")
            limit_str = Prompt.ask("Max items to fetch (0 for all)", default="20")
            limit = int(limit_str) if limit_str.isdigit() else 20

            from core.discovery import dump_hot_board

            base_path = Path(self.config.get("path") or "./Downloaded/")

            console.print("[info]Fetching Douyin hot board...[/info]")
            async with DouyinAPIClient(self.cookie_manager.get_cookies()) as api_client:
                # Direct live.douyin.com override for webcast compatibility (in case needed elsewhere)
                res = await dump_hot_board(api_client, base_path, limit=limit)
                console.print(
                    f"[bold green]✓ Hot search board saved! Count: {res['count']} -> {res['path']}[/bold green]"
                )
            Prompt.ask("\n[dim]Press Enter to return...[/dim]")

        elif choice == 1:
            console.clear()
            console.print(self.get_header())
            console.print("\n[bold cyan]🔍 Search Works by Keyword[/bold cyan]\n")
            kw = Prompt.ask("Enter search keyword").strip()
            if kw:
                max_items_str = Prompt.ask("Max search results", default="30")
                max_items = int(max_items_str) if max_items_str.isdigit() else 30

                from core.discovery import search_and_dump

                base_path = Path(self.config.get("path") or "./Downloaded/")

                console.print(f"[info]Searching for '{kw}'...[/info]")
                async with DouyinAPIClient(self.cookie_manager.get_cookies()) as api_client:
                    res = await search_and_dump(api_client, kw, base_path, max_items=max_items)
                    console.print(
                        f"[bold green]✓ Search results saved! Count: {res['count']} -> {res['path']}[/bold green]"
                    )
            Prompt.ask("\n[dim]Press Enter to return...[/dim]")

    async def handle_cookie_setup(self):
        cookie_options = [
            ("🔍", "Check Current Cookie Status", "Inspect existing cookie fields and validity"),
            (
                "🌐",
                "Launch Playwright Auto Login",
                "Open browser to log in interactively & capture cookies",
            ),
            (
                "📝",
                "Paste Cookie String / JSON",
                "Manually paste raw cookie string or JSON dictionary",
            ),
            ("🔙", "Back to Main Menu", "Return to main menu"),
        ]
        choice = self.select_menu(cookie_options, title="Session & Authentication Setup")

        if choice == 0:
            console.clear()
            console.print(self.get_header())
            console.print("\n[bold cyan]🔍 Current Cookie Inspection[/bold cyan]\n")
            cookies = self.cookie_manager.get_cookies()
            if not cookies:
                console.print("[bold red]No cookies currently loaded in configuration.[/bold red]")
            else:
                table = Table(title="Cookie Keys", box=None)
                table.add_column("Key Name", style="cyan")
                table.add_column("Status / Value Preview", style="yellow")

                req_keys = {"msToken", "ttwid", "odin_tt", "passport_csrf_token"}
                for k, v in cookies.items():
                    tag = " [bold green](Required)[/bold green]" if k in req_keys else ""
                    val_preview = v[:20] + "..." if len(str(v)) > 20 else str(v)
                    table.add_row(k + tag, val_preview)
                console.print(table)
            Prompt.ask("\n[dim]Press Enter to return...[/dim]")

        elif choice == 1:
            console.clear()
            console.print(self.get_header())
            console.print("\n[bold cyan]🌐 Playwright Auto Login Tool[/bold cyan]\n")
            await self.handle_run_cookie_fetcher()
            Prompt.ask("\n[dim]Press Enter to return...[/dim]")

        elif choice == 2:
            console.clear()
            console.print(self.get_header())
            console.print("\n[bold cyan]📝 Paste Cookie String / JSON[/bold cyan]\n")
            raw = Prompt.ask("Paste raw Cookie Header or JSON").strip()
            if raw:
                from utils.cookie_utils import parse_cookie_header, sanitize_cookies

                new_cookies = {}
                if raw.startswith("{"):
                    try:
                        new_cookies = json.loads(raw)
                    except Exception:
                        pass
                if not new_cookies:
                    new_cookies = parse_cookie_header(raw)

                if new_cookies:
                    sanitized = sanitize_cookies(new_cookies)
                    self.config.update(cookies=sanitized)
                    self.config.save()
                    self.cookie_manager.set_cookies(sanitized)
                    console.print(
                        f"[bold green]✓ Updated {len(sanitized)} cookie keys successfully![/bold green]"
                    )
                else:
                    console.print("[bold red]Could not parse valid cookies from input.[/bold red]")
            Prompt.ask("\n[dim]Press Enter to return...[/dim]")

    async def handle_settings(self):
        console.clear()
        console.print(self.get_header())
        console.print("\n[bold cyan]⚙️ Application Settings[/bold cyan]\n")

        cur_path = self.config.get("path") or "./Downloaded/"
        cur_thread = self.config.get("thread", 5)
        cur_rate = self.config.get("rate_limit", 2)
        cur_db = self.config.get("database", True)
        cur_folderstyle = self.config.get("folderstyle", True)

        console.print(f"1. Download Save Path: [cyan]{cur_path}[/cyan]")
        console.print(f"2. Concurrency Threads: [yellow]{cur_thread}[/yellow]")
        console.print(f"3. Rate Limit (req/sec): [yellow]{cur_rate}[/yellow]")
        console.print(f"4. Database Enabled: [green]{cur_db}[/green]")
        console.print(f"5. FolderStyle Subdirectories: [green]{cur_folderstyle}[/green]\n")

        if Confirm.ask("Would you like to modify these settings?"):
            new_path = Prompt.ask(
                "New Save Path (press Enter to keep current)", default=str(cur_path)
            )
            new_thread = Prompt.ask("Concurrency Threads", default=str(cur_thread))
            new_rate = Prompt.ask("Rate Limit", default=str(cur_rate))
            new_db = Confirm.ask("Enable Database history tracking?", default=bool(cur_db))
            new_folderstyle = Confirm.ask(
                "Use FolderStyle subdirectories?", default=bool(cur_folderstyle)
            )

            self.config.update(
                path=new_path,
                thread=int(new_thread) if new_thread.isdigit() else cur_thread,
                rate_limit=float(new_rate)
                if new_rate.replace(".", "", 1).isdigit()
                else cur_rate,
                database=new_db,
                folderstyle=new_folderstyle,
            )
            self.config.save()
            console.print("\n[bold green]✓ Configuration saved successfully![/bold green]")

        Prompt.ask("\n[dim]Press Enter to return to main menu...[/dim]")

    async def handle_history_paging(self):
        """Displays download history paginated and supports filtration."""
        if not self.config.get("database"):
            console.clear()
            console.print(self.get_header())
            console.print(
                "\n[yellow]⚠️ Database history tracking is currently disabled in settings.[/yellow]"
            )
            Prompt.ask("\n[dim]Press Enter to return...[/dim]")
            return

        db_path = self.config.get("database_path", "dy_downloader.db") or "dy_downloader.db"
        if not Path(db_path).exists():
            console.clear()
            console.print(self.get_header())
            console.print(f"\n[yellow]⚠️ Database file does not exist yet at {db_path}.[/yellow]")
            Prompt.ask("\n[dim]Press Enter to return...[/dim]")
            return

        database = Database(db_path=str(db_path))
        await database.initialize()

        page = 1
        size = 10
        filter_kw = None

        while True:
            console.clear()
            console.print(self.get_header())
            console.print(
                f"\n[bold cyan]🛠️ SQLite Database Explorer[/bold cyan] [dim](Page {page})[/dim]\n"
            )

            res = await database.get_aweme_history(page=page, size=size, title=filter_kw)
            total = res.get("total", 0)
            items = res.get("items", [])
            max_pages = max(1, (total + size - 1) // size)

            if filter_kw:
                console.print(
                    f"[cyan]Active Filter:[/cyan] keyword matches '[yellow]{filter_kw}[/yellow]'"
                )
            console.print(f"Total downloaded records: [bold yellow]{total}[/bold yellow]\n")

            if not items:
                console.print("[dim]No records found on this page.[/dim]\n")
            else:
                table = Table(box=None, expand=True)
                table.add_column("ID / Type", style="cyan")
                table.add_column("Title / Content", style="white")
                table.add_column("Author", style="yellow")
                table.add_column("Downloaded At", style="green")

                for idx, item in enumerate(items, start=(page - 1) * size + 1):
                    title = item.get("title") or ""
                    short_title = title[:45] + "..." if len(title) > 45 else title

                    import datetime

                    ts = item.get("download_time", 0)
                    dt_str = (
                        datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                        if ts
                        else "-"
                    )

                    table.add_row(
                        f"#{idx}\n[dim]{item.get('aweme_type', 'video')}[/dim]",
                        f"{short_title}\n[dim]{item.get('aweme_id')}[/dim]",
                        item.get("author_name", "Unknown"),
                        dt_str,
                    )
                console.print(table)

            console.print(
                "\n[bold cyan]Controls:[/bold cyan] [bold yellow][N][/bold yellow]ext Page | "
                "[bold yellow][P][/bold yellow]rev Page | "
                "[bold yellow][F][/bold yellow]ilter | "
                "[bold yellow][C][/bold yellow]lear Filter | "
                "[bold yellow][B][/bold yellow]ack to Menu"
            )

            key = get_key().lower()
            if key == "n" and page < max_pages:
                page += 1
            elif key == "p" and page > 1:
                page -= 1
            elif key == "f":
                new_kw = Prompt.ask("\nEnter search filter keyword").strip()
                if new_kw:
                    filter_kw = new_kw
                    page = 1
            elif key == "c":
                filter_kw = None
                page = 1
            elif key == "b" or key == "escape":
                break

        await database.close()

    async def handle_diagnostics(self):
        console.clear()
        console.print(self.get_header())
        console.print("\n[bold cyan]🩺 Self-Diagnostic Test Suite[/bold cyan]\n")

        config_status = (
            "[green]✓ Config Valid[/green]"
            if self.config.validate()
            else "[red]✗ Config Invalid[/red]"
        )
        console.print(f"Checking {self.config_path} layout... {config_status}")

        cookies = self.config.get_cookies()
        if not cookies:
            cookie_status = "[red]✗ Missing entirely[/red]"
        elif not cookies.get("msToken"):
            cookie_status = "[yellow]! msToken Missing[/yellow]"
        elif self.cookie_manager.validate_cookies():
            cookie_status = "[green]✓ Valid & Active[/green]"
        else:
            cookie_status = "[yellow]! Validation Warning (Possible Expiry)[/yellow]"
        console.print(f"Checking Douyin authentication cookies... {cookie_status}")

        db_path = self.config.get("database_path", "dy_downloader.db") or "dy_downloader.db"
        try:
            database = Database(db_path=str(db_path))
            await database.initialize()
            await database.close()
            db_status = f"[green]✓ Connected & Initialized ({db_path})[/green]"
        except Exception as e:
            db_status = f"[red]✗ Database Error: {e}[/red]"
        console.print(f"Checking SQLite history database access... {db_status}")

        try:
            import playwright  # noqa: F401

            pw_status = "[green]✓ Installed[/green]"
        except ImportError:
            pw_status = "[yellow]! Playwright not installed (browser fallback disabled)[/yellow]"
        console.print(f"Checking Playwright installation... {pw_status}")

        try:
            import whisper  # noqa: F401

            whisper_status = "[green]✓ Installed[/green]"
        except ImportError:
            whisper_status = "[dim]Not installed (Whisper transcriptions disabled)[/dim]"
        console.print(f"Checking Whisper OpenAI transcription tool... {whisper_status}")

        Prompt.ask("\n[bold cyan]Press Enter to return to main menu...[/bold cyan]")


def main():
    config_path = "config.yml"
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        config_path = sys.argv[1]
    manager = DouyinMenuManager(config_path=config_path)
    try:
        asyncio.run(manager.start())
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Menu exited by user.[/bold yellow]")


if __name__ == "__main__":
    main()
