import os
import re
import time
import json
import glob
import shutil
import subprocess
import logging
import psutil
import httpx
import aiosqlite
from typing import Optional, Dict, Any, List, Tuple, Union
from config import Config

log = logging.getLogger("agent_terminal.steam")

# Pre-compiled regexes for VDF, ACF, Requirements, and BBCode parsing
_RE_VDF_PATH = re.compile(r'"path"\s+"([^"]+)"')
_RE_ACF_APPID = re.compile(r'"appid"\s+"(\d+)"')
_RE_ACF_NAME = re.compile(r'"name"\s+"([^"]+)"')
_RE_ACF_SIZE = re.compile(r'"SizeOnDisk"\s+"(\d+)"')
_RE_ACF_INSTALLDIR = re.compile(r'"installdir"\s+"([^"]+)"')

_RE_REQ_HEADER = re.compile(r'<strong>\s*(Minimum|Recommended)\s*:?\s*</strong>', flags=re.IGNORECASE)
_RE_BR = re.compile(r'<br\s*/?>', flags=re.IGNORECASE)
_RE_LI = re.compile(r'<li>(.*?)</li>', flags=re.DOTALL | re.IGNORECASE)
_RE_STRONG = re.compile(r'<strong>\s*(.*?)\s*</strong>', flags=re.DOTALL | re.IGNORECASE)
_RE_HTML_TAGS = re.compile(r'<.*?>')
_RE_WHITESPACE = re.compile(r'\s+')

_RE_BBCODE = re.compile(r"\[/?(b|i|url|quote|img|list|\*)[^\]]*\]", flags=re.IGNORECASE)


class SteamClient:
    """Handles interaction with Steam Web API, Steam Store API, ProtonDB,
    and local Arch Linux Steam libraries with persistent connection pooling and SQLite caching."""

    def __init__(self, api_key: Optional[str] = None, steam_id: Optional[str] = None, db_path: Optional[str] = None):
        self.api_key = api_key or Config.STEAM_API_KEY
        self.steam_id = steam_id or Config.STEAM_ID
        self.base_url = "https://api.steampowered.com"
        self.db_path = db_path or Config.DB_PATH
        self._cache_initialized = False
        self._http_client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.aclose()

    def _get_http_client(self) -> httpx.AsyncClient:
        """Returns a shared httpx.AsyncClient instance with connection pooling."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=15),
                headers={"User-Agent": "ArchAITerminal/2.0 (Linux; Arch Linux)"}
            )
        return self._http_client

    async def aclose(self):
        """Closes the underlying HTTP client session."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def _init_cache_db(self):
        """Initializes the SQLite cache tables for game reports and API responses."""
        if self._cache_initialized:
            return
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS steam_game_cache (
                        appid INTEGER PRIMARY KEY,
                        name TEXT,
                        report TEXT,
                        cached_at REAL
                    )
                ''')
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS steam_kv_cache (
                        cache_key TEXT PRIMARY KEY,
                        cache_data TEXT,
                        cached_at REAL
                    )
                ''')
                await db.commit()
            self._cache_initialized = True
        except Exception as e:
            log.warning(f"Failed to initialize steam cache DB: {e}")

    async def _get_cached_report(self, appid: int, max_age: float = 86400) -> Optional[str]:
        """Retrieves cached report if within max_age TTL (default 24h)."""
        await self._init_cache_db()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("SELECT report, cached_at FROM steam_game_cache WHERE appid = ?", (appid,)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        report, cached_at = row
                        if (time.time() - cached_at) < max_age:
                            return report
        except Exception as e:
            log.warning(f"Error reading steam report cache: {e}")
        return None

    async def _save_cached_report(self, appid: int, name: str, report: str):
        """Saves a report to the SQLite cache."""
        await self._init_cache_db()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO steam_game_cache (appid, name, report, cached_at) VALUES (?, ?, ?, ?)",
                    (appid, name, report, time.time())
                )
                await db.commit()
        except Exception as e:
            log.warning(f"Error saving steam report cache: {e}")

    async def _get_cached_kv(self, key: str, max_age: float = 3600) -> Optional[Any]:
        """Retrieves cached structured JSON data by key."""
        await self._init_cache_db()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("SELECT cache_data, cached_at FROM steam_kv_cache WHERE cache_key = ?", (key,)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        data_str, cached_at = row
                        if (time.time() - cached_at) < max_age:
                            return json.loads(data_str)
        except Exception as e:
            log.warning(f"Error reading steam KV cache: {e}")
        return None

    async def _save_cached_kv(self, key: str, data: Any):
        """Saves structured JSON data to the KV cache."""
        await self._init_cache_db()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO steam_kv_cache (cache_key, cache_data, cached_at) VALUES (?, ?, ?)",
                    (key, json.dumps(data), time.time())
                )
                await db.commit()
        except Exception as e:
            log.warning(f"Error saving steam KV cache: {e}")

    async def clear_cache(self) -> bool:
        """Clears all Steam cache entries."""
        await self._init_cache_db()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM steam_game_cache")
                await db.execute("DELETE FROM steam_kv_cache")
                await db.commit()
            return True
        except Exception as e:
            log.warning(f"Error clearing steam cache: {e}")
            return False

    async def get_owned_games(self, steam_id: Optional[str] = None, use_cache: bool = True) -> Optional[Dict[str, Any]]:
        """Fetches a list of owned games for a given Steam ID with SQLite caching."""
        target_id = steam_id or self.steam_id
        if not self.api_key or not target_id:
            return None

        cache_key = f"owned_games:{target_id}"
        if use_cache:
            cached = await self._get_cached_kv(cache_key, max_age=3600)
            if cached:
                return cached

        url = f"{self.base_url}/IPlayerService/GetOwnedGames/v1/"
        params = {
            "key": self.api_key,
            "steamid": target_id,
            "include_appinfo": 1,
            "include_played_free_games": 1,
            "format": "json"
        }

        try:
            client = self._get_http_client()
            response = await client.get(url, params=params)
            if response.status_code == 200:
                data = response.json().get("response", {})
                await self._save_cached_kv(cache_key, data)
                return data
            else:
                log.error(f"Steam API returned status {response.status_code}: {response.text}")
        except Exception as e:
            log.error(f"Steam API Error (GetOwnedGames): {e}")
        return None

    async def get_recently_played_games(self, steam_id: Optional[str] = None, count: int = 5, use_cache: bool = True) -> Optional[List[Dict[str, Any]]]:
        """Fetches recently played games for a given Steam ID."""
        target_id = steam_id or self.steam_id
        if not self.api_key or not target_id:
            return None

        cache_key = f"recent_games:{target_id}:{count}"
        if use_cache:
            cached = await self._get_cached_kv(cache_key, max_age=600)
            if cached:
                return cached

        url = f"{self.base_url}/IPlayerService/GetRecentlyPlayedGames/v1/"
        params = {
            "key": self.api_key,
            "steamid": target_id,
            "count": count,
            "format": "json"
        }

        try:
            client = self._get_http_client()
            response = await client.get(url, params=params)
            if response.status_code == 200:
                games = response.json().get("response", {}).get("games", [])
                await self._save_cached_kv(cache_key, games)
                return games
        except Exception as e:
            log.error(f"Steam API Error (GetRecentlyPlayedGames): {e}")
        return None

    async def get_game_details(self, appid: int, use_cache: bool = True) -> Optional[Dict[str, Any]]:
        """Fetches store details for a specific game with SQLite caching."""
        cache_key = f"game_details:{appid}"
        if use_cache:
            cached = await self._get_cached_kv(cache_key, max_age=604800)  # 7 days
            if cached:
                return cached

        url = "https://store.steampowered.com/api/appdetails"
        params = {"appids": appid}

        try:
            client = self._get_http_client()
            response = await client.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                if data and str(appid) in data and data[str(appid)].get("success"):
                    details = data[str(appid)]["data"]
                    await self._save_cached_kv(cache_key, details)
                    return details
        except Exception as e:
            log.error(f"Steam Store API Error: {e}")
        return None

    async def get_protondb_summary(self, appid: int, use_cache: bool = True) -> Optional[Dict[str, Any]]:
        """Fetches community Proton compatibility ratings and statistics from ProtonDB API."""
        cache_key = f"protondb:{appid}"
        if use_cache:
            cached = await self._get_cached_kv(cache_key, max_age=86400)
            if cached:
                return cached

        url = f"https://www.protondb.com/api/v1/reports/summaries/{appid}.json"
        try:
            client = self._get_http_client()
            response = await client.get(url)
            if response.status_code == 200:
                data = response.json()
                await self._save_cached_kv(cache_key, data)
                return data
        except Exception as e:
            log.warning(f"ProtonDB API fetch failed for {appid}: {e}")
        return None

    async def get_game_news(self, appid: int, count: int = 3, use_cache: bool = True) -> List[Dict[str, Any]]:
        """Fetches recent news items and patch notes for a game from Steam."""
        cache_key = f"news:{appid}:{count}"
        if use_cache:
            cached = await self._get_cached_kv(cache_key, max_age=21600)  # 6 hours
            if cached is not None:
                return cached

        url = f"{self.base_url}/ISteamNews/GetNewsForApp/v2/"
        params = {"appid": appid, "count": count, "maxlength": 400, "format": "json"}
        try:
            client = self._get_http_client()
            response = await client.get(url, params=params)
            if response.status_code == 200:
                items = response.json().get("appnews", {}).get("newsitems", [])
                await self._save_cached_kv(cache_key, items)
                return items
        except Exception as e:
            log.error(f"Steam News API Error: {e}")
        return []

    async def search_store(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Searches the Steam Store for games by title (does not require API key)."""
        clean_q = query.strip()
        if not clean_q:
            return []

        cache_key = f"store_search:{clean_q.lower()}:{limit}"
        cached = await self._get_cached_kv(cache_key, max_age=86400)
        if cached is not None:
            return cached

        url = "https://store.steampowered.com/api/storesearch/"
        params = {"term": clean_q, "l": "english", "cc": "US"}

        try:
            client = self._get_http_client()
            response = await client.get(url, params=params)
            if response.status_code == 200:
                items = response.json().get("items", [])
                results = []
                for it in items[:limit]:
                    price_info = it.get("price")
                    price_str = "Free"
                    if price_info:
                        cents = price_info.get("final", 0)
                        price_str = f"${cents / 100:.2f}" if cents > 0 else "Free"
                    results.append({
                        "appid": it.get("id"),
                        "name": it.get("name", "Unknown"),
                        "price": price_str
                    })
                await self._save_cached_kv(cache_key, results)
                return results
        except Exception as e:
            log.error(f"Steam store search failed: {e}")
        return []

    # ==========================================
    # Local Arch Linux Steam Libraries & System
    # ==========================================

    @staticmethod
    def find_steam_libraries() -> List[str]:
        """Discovers all local Steam library folders on the system."""
        roots = [
            os.path.expanduser("~/.local/share/Steam"),
            os.path.expanduser("~/.steam/steam"),
            os.path.expanduser("~/.steam/root"),
        ]
        seen = set()
        steamapps_dirs = []

        for r in roots:
            if not os.path.isdir(r):
                continue
            vdf_path = os.path.join(r, "steamapps", "libraryfolders.vdf")
            if os.path.isfile(vdf_path):
                try:
                    with open(vdf_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        for p in _RE_VDF_PATH.findall(content):
                            real_p = os.path.realpath(p)
                            sapps = os.path.join(real_p, "steamapps")
                            if os.path.isdir(sapps) and sapps not in seen:
                                seen.add(sapps)
                                steamapps_dirs.append(sapps)
                except Exception:
                    pass

            default_sapps = os.path.realpath(os.path.join(r, "steamapps"))
            if os.path.isdir(default_sapps) and default_sapps not in seen:
                seen.add(default_sapps)
                steamapps_dirs.append(default_sapps)

        return steamapps_dirs

    def get_installed_games(self) -> List[Dict[str, Any]]:
        """Parses local appmanifest files to discover locally installed Steam games."""
        lib_dirs = self.find_steam_libraries()
        installed_games = {}

        for sdir in lib_dirs:
            pattern = os.path.join(sdir, "appmanifest_*.acf")
            for f in glob.glob(pattern):
                try:
                    with open(f, "r", encoding="utf-8", errors="ignore") as fp:
                        content = fp.read()
                        appid_m = _RE_ACF_APPID.search(content)
                        name_m = _RE_ACF_NAME.search(content)
                        size_m = _RE_ACF_SIZE.search(content)
                        installdir_m = _RE_ACF_INSTALLDIR.search(content)

                        if appid_m and name_m:
                            aid = int(appid_m.group(1))
                            size_gb = round(int(size_m.group(1)) / (1024 ** 3), 1) if size_m else 0.0
                            if aid not in installed_games or size_gb > installed_games[aid]["size_gb"]:
                                installed_games[aid] = {
                                    "appid": aid,
                                    "name": name_m.group(1),
                                    "size_gb": size_gb,
                                    "installdir": installdir_m.group(1) if installdir_m else "",
                                    "library": sdir
                                }
                except Exception:
                    pass

        return sorted(installed_games.values(), key=lambda g: g["size_gb"], reverse=True)

    def get_steam_status(self) -> Dict[str, Any]:
        """Inspects the local system for Steam client state, installed Proton versions, and gaming packages."""
        steam_pids = []
        try:
            for p in psutil.process_iter(["pid", "name"]):
                if p.info.get("name") == "steam":
                    steam_pids.append(p.info["pid"])
        except Exception:
            pass

        # Detect Proton runtimes
        protons = set()
        compat_dirs = [
            os.path.expanduser("~/.local/share/Steam/compatibilitytools.d"),
            os.path.expanduser("~/.local/share/Steam/steamapps/common"),
            os.path.expanduser("~/.steam/steam/compatibilitytools.d"),
            os.path.expanduser("~/.steam/steam/steamapps/common"),
        ]
        for cdir in compat_dirs:
            if os.path.isdir(cdir):
                for entry in os.listdir(cdir):
                    if "proton" in entry.lower():
                        protons.add(entry)

        # Detect Linux gaming helper utilities
        gaming_tools = {
            "gamemode": bool(shutil.which("gamemoderun")),
            "mangohud": bool(shutil.which("mangohud")),
            "gamescope": bool(shutil.which("gamescope")),
            "wine": bool(shutil.which("wine")),
        }

        # Detect GPU
        gpu = "Unknown"
        if os.path.exists("/dev/nvidia0"):
            gpu = "NVIDIA (Proprietary / Open Kernel Driver)"
        elif os.path.exists("/dev/dri/card0"):
            gpu = "AMD / Intel Vulkan"

        return {
            "is_running": len(steam_pids) > 0,
            "steam_pid": steam_pids[0] if steam_pids else None,
            "installed_protons": sorted(list(protons)),
            "gaming_tools": gaming_tools,
            "gpu": gpu,
            "installed_game_count": len(self.get_installed_games())
        }

    async def resolve_game(self, query: str) -> Optional[Dict[str, Any]]:
        """Resolves an AppID, game title, or search term to a game dictionary with appid and name."""
        clean_q = query.strip()
        if not clean_q:
            return None

        # 1. Direct AppID
        if clean_q.isdigit():
            aid = int(clean_q)
            details = await self.get_game_details(aid)
            name = details.get("name", f"AppID {aid}") if details else f"AppID {aid}"
            return {"appid": aid, "name": name, "source": "appid"}

        # 2. Check local installed games first
        installed = self.get_installed_games()
        for g in installed:
            if clean_q.lower() == g["name"].lower():
                return {"appid": g["appid"], "name": g["name"], "source": "installed"}

        # 3. Check user's owned games library
        owned = await self.get_owned_games()
        if owned and "games" in owned:
            exact = [g for g in owned["games"] if clean_q.lower() == g.get("name", "").lower()]
            if exact:
                return {"appid": exact[0]["appid"], "name": exact[0]["name"], "source": "owned"}

            partial = [g for g in owned["games"] if clean_q.lower() in g.get("name", "").lower()]
            if partial:
                partial.sort(key=lambda g: g.get("playtime_forever", 0), reverse=True)
                return {"appid": partial[0]["appid"], "name": partial[0]["name"], "source": "owned"}

        # Substring in installed games
        part_inst = [g for g in installed if clean_q.lower() in g["name"].lower()]
        if part_inst:
            return {"appid": part_inst[0]["appid"], "name": part_inst[0]["name"], "source": "installed"}

        # 4. Search the Steam Store
        store_items = await self.search_store(clean_q, limit=3)
        if store_items:
            return {"appid": store_items[0]["appid"], "name": store_items[0]["name"], "source": "store"}

        return None

    def launch_game(self, appid_or_name: str) -> Tuple[bool, str]:
        """Launches a game via the Steam client protocol in the background."""
        clean = appid_or_name.strip()
        if clean.isdigit():
            aid = int(clean)
            name = f"AppID {aid}"
        else:
            inst = self.get_installed_games()
            match = next((g for g in inst if clean.lower() in g["name"].lower()), None)
            if not match:
                return False, f"Could not find installed game matching '{appid_or_name}'."
            aid = match["appid"]
            name = match["name"]

        if not shutil.which("steam"):
            return False, "Steam executable not found in PATH."

        try:
            subprocess.Popen(
                ["steam", f"steam://rungameid/{aid}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return True, f"Launched **{name}** (AppID: {aid}) via Steam."
        except Exception as e:
            return False, f"Failed to launch game: {e}"

    # ==========================================
    # Markdown Formatting
    # ==========================================

    async def format_owned_games_summary(self, steam_id: Optional[str] = None, limit: int = 15) -> str:
        """Returns a formatted Markdown summary of owned games with playtimes."""
        data = await self.get_owned_games(steam_id)
        if not data or "games" not in data:
            if not self.api_key or not (steam_id or self.steam_id):
                return "Steam configuration incomplete. Set STEAM_API_KEY and STEAM_ID in your .env file (or use /steam installed for local games)."
            return "No Steam games found or profile is private."

        games = sorted(data["games"], key=lambda g: g.get("playtime_forever", 0), reverse=True)
        total_games = len(games)
        displayed_games = games[:limit]

        lines = [
            f"**Steam Library Overview** ({total_games} total games)\n",
            "| AppID | Game Title | Playtime |",
            "| :--- | :--- | :--- |"
        ]

        for g in displayed_games:
            hours = round(g.get("playtime_forever", 0) / 60, 1)
            lines.append(f"| `{g.get('appid')}` | {g.get('name', 'Unknown')} | {hours} hrs |")

        lines.append("\n*(Type `/steam <appid or name>` for compatibility, or `/steam installed` for local games)*")
        return "\n".join(lines)

    def format_installed_games_summary(self, limit: int = 20) -> str:
        """Returns a formatted Markdown summary of locally installed games with disk usage."""
        games = self.get_installed_games()
        if not games:
            return "No locally installed Steam games found in standard Arch Linux directories."

        total_games = len(games)
        total_gb = round(sum(g["size_gb"] for g in games), 1)
        displayed = games[:limit]

        lines = [
            f"**Locally Installed Steam Games** ({total_games} games, {total_gb} GB total disk space)\n",
            "| AppID | Game Title | Size | Install Directory |",
            "| :--- | :--- | :--- | :--- |"
        ]

        for g in displayed:
            lines.append(f"| `{g['appid']}` | {g['name']} | {g['size_gb']} GB | `{g['installdir']}` |")

        lines.append("\n*(Use `/steam <name>` for Proton report, or `/steam launch <name>` to launch)*")
        return "\n".join(lines)

    def _clean_requirements(self, html_str: str) -> str:
        """Cleans raw Steam HTML requirements into neat Markdown list items without blank gaps."""
        if not html_str:
            return ""
        html_str = _RE_REQ_HEADER.sub('', html_str)
        html_str = _RE_BR.sub('\n', html_str)

        items = _RE_LI.findall(html_str)
        if items:
            cleaned_items = []
            for it in items:
                it = _RE_STRONG.sub(r'**\1**', it)
                it = _RE_HTML_TAGS.sub('', it)
                it = _RE_WHITESPACE.sub(' ', it).strip()
                if it:
                    cleaned_items.append(f"- {it}")
            if cleaned_items:
                return "\n".join(cleaned_items)

        clean = _RE_HTML_TAGS.sub(' ', html_str)
        lines = [_RE_WHITESPACE.sub(' ', line).strip() for line in clean.splitlines() if line.strip()]
        return "\n".join([f"- {l}" for l in lines])

    async def format_game_report(self, appid_or_name: Union[int, str], use_cache: bool = True) -> str:
        """Returns a comprehensive diagnostic and compatibility report for Arch Linux gaming."""
        if isinstance(appid_or_name, str) and not appid_or_name.isdigit():
            resolved = await self.resolve_game(appid_or_name)
            if not resolved:
                return f"Could not find game matching '{appid_or_name}'."
            appid = resolved["appid"]
        else:
            appid = int(appid_or_name)

        if use_cache:
            cached = await self._get_cached_report(appid)
            if cached:
                return cached + "\n\n*(⚡ Cached Report)*"

        details = await self.get_game_details(appid)
        proton = await self.get_protondb_summary(appid)

        name = details.get("name", f"AppID {appid}") if details else f"AppID {appid}"
        genres = ", ".join([g.get("description", "") for g in details.get("genres", [])]) if details else "N/A"

        tier = "Unknown"
        confidence = "N/A"
        trending = "N/A"
        score_pct = ""
        total_reports = ""

        if proton:
            tier = proton.get("tier", "unknown").upper()
            confidence = proton.get("confidence", "N/A")
            trending = proton.get("trendingTier", "unknown").upper()
            if "score" in proton:
                score_pct = f" (Score: {int(proton['score'] * 100)}%)"
            if "total" in proton:
                total_reports = f" from {proton['total']} reports"

        tier_badge = {
            "NATIVE": "**NATIVE (Linux)**",
            "PLATINUM": "**PLATINUM (Flawless)**",
            "GOLD": "**GOLD (Runs great after tweaks)**",
            "SILVER": "**SILVER (Minor issues)**",
            "BRONZE": "**BRONZE (Frequent crashes)**",
            "BORKED": "**BORKED (Unplayable)**"
        }.get(tier, f"**{tier}**")

        # Check local installation
        installed_games = {g["appid"]: g for g in self.get_installed_games()}
        is_installed = appid in installed_games
        inst_text = f"Installed ✅ ({installed_games[appid]['size_gb']} GB)" if is_installed else "Not installed"

        report = [
            f"### 🎮 {name} (AppID: {appid})",
            f"- **Genre**: {genres}",
            f"- **ProtonDB Rating**: {tier_badge} (Trending: **{trending}**{score_pct}{total_reports})",
            f"- **Local Installation**: {inst_text}",
            "",
            "#### 🐧 Recommended Arch Linux Launch Parameters:",
            "```bash",
            "gamemoderun mangohud %command%",
            "```",
            "*(Requires `gamemode` and `mangohud` packages: `pacman -S gamemode mangohud`)*",
            "",
            "#### 💡 Optimization & Compatibility Tips:",
            "- For DirectX 11/12 games, ensure `vulkan-radeon` (AMD) or `nvidia-utils` (Nvidia) is installed.",
            "- Use GE-Proton (`proton-ge-custom-bin` from AUR) if running into video playback or anti-cheat issues."
        ]

        if os.path.exists("/dev/nvidia0"):
            report.append("- **NVIDIA Hardware Detected**: Add `PROTON_ENABLE_NVAPI=1` for DLSS and Reflex support.")

        if is_installed:
            report.extend(["", f"*(💡 Quick Launch: type `/steam launch {appid}` to play now)*"])

        if details and "pc_requirements" in details:
            min_req_raw = details["pc_requirements"].get("minimum", "")
            if min_req_raw:
                cleaned_req = self._clean_requirements(min_req_raw)
                if cleaned_req:
                    report.extend(["", "#### 📋 Minimum Requirements:", cleaned_req])

        final_report = "\n".join(report)
        await self._save_cached_report(appid, name, final_report)
        return final_report

    async def format_game_news(self, appid_or_name: Union[int, str], count: int = 3) -> str:
        """Returns a formatted Markdown summary of recent news and patch notes."""
        if isinstance(appid_or_name, str) and not appid_or_name.isdigit():
            resolved = await self.resolve_game(appid_or_name)
            if not resolved:
                return f"Could not find game matching '{appid_or_name}'."
            appid = resolved["appid"]
            name = resolved["name"]
        else:
            appid = int(appid_or_name)
            details = await self.get_game_details(appid)
            name = details.get("name", f"AppID {appid}") if details else f"AppID {appid}"

        items = await self.get_game_news(appid, count=count)
        if not items:
            return f"No recent news or patch notes found for **{name}** (AppID: {appid})."

        lines = [f"### 📰 Latest News & Patch Notes: {name} (AppID: {appid})\n"]
        for it in items:
            title = it.get("title", "Update")
            author = it.get("author", "Steam")
            url = it.get("url", "")
            date_str = time.strftime("%Y-%m-%d", time.gmtime(it.get("date", 0)))
            contents = it.get("contents", "").strip()

            clean_content = _RE_BBCODE.sub("", contents)
            clean_content = _RE_HTML_TAGS.sub("", clean_content)
            clean_content = _RE_WHITESPACE.sub(" ", clean_content).strip()
            if len(clean_content) > 300:
                clean_content = clean_content[:290] + "..."

            lines.append(f"#### [{title}]({url})")
            lines.append(f"*{date_str} by {author}*")
            if clean_content:
                lines.append(f"> {clean_content}")
            lines.append("")

        return "\n".join(lines)

    async def format_store_search(self, query: str, limit: int = 5) -> str:
        """Returns a Markdown table of games from the Steam store search."""
        items = await self.search_store(query, limit=limit)
        if not items:
            return f"No Steam store results found matching '{query}'."

        lines = [
            f"**Steam Store Search Results** for *'{query}'*\n",
            "| AppID | Title | Price |",
            "| :--- | :--- | :--- |"
        ]
        for it in items:
            lines.append(f"| `{it['appid']}` | {it['name']} | {it['price']} |")

        lines.append("\n*(Type `/steam <appid>` for compatibility report, or `/steam news <appid>` for patch notes)*")
        return "\n".join(lines)

    def format_steam_status(self) -> str:
        """Returns a comprehensive diagnostic status of Steam, Proton, and gaming tools on Arch Linux."""
        status = self.get_steam_status()
        steam_indicator = "🟢 Running" if status["is_running"] else "⚪ Stopped"
        pid_info = f" (PID: {status['steam_pid']})" if status["steam_pid"] else ""

        lines = [
            "### 🕹️ Arch Linux Gaming & Steam Status\n",
            f"- **Steam Client:** {steam_indicator}{pid_info}",
            f"- **Graphics Hardware:** {status['gpu']}",
            f"- **Installed Games:** {status['installed_game_count']} locally installed",
            "",
            "#### 🍷 Installed Proton Versions:"
        ]

        if status["installed_protons"]:
            for p in status["installed_protons"]:
                lines.append(f"- `✓` {p}")
        else:
            lines.append("- *No Proton runtimes detected in Steam directories.*")

        lines.extend([
            "",
            "#### 🛠️ Linux Gaming Helpers (Arch Packages):"
        ])
        for tool, installed in status["gaming_tools"].items():
            icon = "✅ Installed" if installed else "⚠️ Missing"
            pkg_hint = "" if installed else f" (`sudo pacman -S {tool}`)"
            lines.append(f"- **{tool}:** {icon}{pkg_hint}")

        return "\n".join(lines)
