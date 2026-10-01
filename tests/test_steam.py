import pytest
from arch_ai.steam_utils import SteamClient


def test_steam_client_initialization():
    client = SteamClient(api_key="test_key", steam_id="test_id")
    assert client.api_key == "test_key"
    assert client.steam_id == "test_id"


@pytest.mark.asyncio
async def test_steam_format_unconfigured():
    client = SteamClient(api_key="", steam_id="")
    client.api_key = None
    client.steam_id = None
    res = await client.format_owned_games_summary(steam_id="")
    assert "Steam configuration incomplete" in res or "No Steam games found" in res


@pytest.mark.asyncio
async def test_steam_format_game_report_defaults(tmp_path):
    db_file = str(tmp_path / "test_steam.db")
    client = SteamClient(api_key=None, steam_id=None, db_path=db_file)
    # First fetch (populates cache)
    report1 = await client.format_game_report(730)
    assert "AppID: 730" in report1
    assert "gamemoderun" in report1

    # Second fetch should hit SQLite cache
    report2 = await client.format_game_report(730, use_cache=True)
    assert "Cached Report" in report2


def test_steam_find_libraries():
    libs = SteamClient.find_steam_libraries()
    assert isinstance(libs, list)


def test_steam_installed_games():
    client = SteamClient(api_key=None, steam_id=None)
    games = client.get_installed_games()
    assert isinstance(games, list)
    if games:
        assert "appid" in games[0]
        assert "name" in games[0]
        assert "size_gb" in games[0]


def test_steam_status():
    client = SteamClient(api_key=None, steam_id=None)
    st = client.get_steam_status()
    assert "is_running" in st
    assert "installed_protons" in st
    assert "gaming_tools" in st
    assert "gamemode" in st["gaming_tools"]
    formatted = client.format_steam_status()
    assert "Arch Linux Gaming & Steam Status" in formatted


@pytest.mark.asyncio
async def test_steam_resolve_by_appid():
    client = SteamClient(api_key=None, steam_id=None)
    res = await client.resolve_game("730")
    assert res is not None
    assert res["appid"] == 730


@pytest.mark.asyncio
async def test_steam_search_store():
    client = SteamClient(api_key=None, steam_id=None)
    results = await client.search_store("witcher", limit=2)
    assert isinstance(results, list)
    if results:
        assert "appid" in results[0]
        assert "name" in results[0]
        assert "price" in results[0]


@pytest.mark.asyncio
async def test_steam_clear_cache(tmp_path):
    db_file = str(tmp_path / "test_cache_clear.db")
    client = SteamClient(api_key=None, steam_id=None, db_path=db_file)
    await client._save_cached_kv("test_key", {"data": 123})
    assert await client._get_cached_kv("test_key") == {"data": 123}
    await client.clear_cache()
    assert await client._get_cached_kv("test_key") is None


def test_steam_launch_game_missing():
    client = SteamClient(api_key=None, steam_id=None)
    success, msg = client.launch_game("nonexistent_game_xyz_99999")
    assert not success
    assert "Could not find installed game" in msg


def test_gaming_optimizer_scan_and_format():
    from arch_ai.gaming import GamingOptimizer

    data = GamingOptimizer.scan_system()
    assert "score" in data
    assert "rating" in data
    assert "cpu" in data
    assert "kernel" in data
    assert "gpu" in data
    assert "daemons" in data
    assert 0 <= data["score"] <= 100

    report = GamingOptimizer.format_scan_report(data)
    assert "Arch Linux Gaming System Diagnostic Report" in report
    assert f"{data['score']}/100" in report


def test_gaming_optimizer_actions():
    from arch_ai.gaming import GamingOptimizer

    success, actions, summary = GamingOptimizer.optimize_system(apply=False)
    assert isinstance(actions, list)
    assert len(actions) > 0
    assert "vm.max_map_count" in summary or "GameMode" in summary


def test_gaming_arcade_spinner_registration():
    from rich.spinner import SPINNERS
    from arch_ai.gaming import GAMING_SPINNER_NAME

    assert GAMING_SPINNER_NAME in SPINNERS
    spinner_def = SPINNERS[GAMING_SPINNER_NAME]
    assert "frames" in spinner_def
    assert any("🎮" in f for f in spinner_def["frames"])
    assert any("🕹️" in f for f in spinner_def["frames"])


@pytest.mark.asyncio
async def test_gaming_companion_query_streaming_and_spinner():
    from unittest.mock import MagicMock, AsyncMock
    from rich.console import Console
    from arch_ai.gaming import GamingCompanion

    mock_client = MagicMock()
    mock_chunk1 = MagicMock()
    mock_chunk1.text = "Recommended launch options: "
    mock_chunk1.candidates = []

    mock_chunk2 = MagicMock()
    mock_chunk2.text = "gamemoderun %command%"
    mock_chunk2.candidates = []

    async def mock_stream_gen():
        yield mock_chunk1
        yield mock_chunk2

    async def mock_stream_func(*args, **kwargs):
        return mock_stream_gen()

    mock_client.aio.models.generate_content_stream = mock_stream_func
    companion = GamingCompanion(console=Console(quiet=True), model=mock_client)
    res = await companion.query("best launch options for Cyberpunk 2077")
    assert res is not None
    assert "gamemoderun %command%" in res
    await companion.aclose()
