import pytest
import os
import tempfile
from arch_ai.analyzer import BehaviorAnalyzer

@pytest.fixture
async def analyzer():
    # Use a temporary database for testing
    fd, path = tempfile.mkstemp()
    os.close(fd)
    analyzer = BehaviorAnalyzer(db_path=path)
    await analyzer.initialize()
    yield analyzer
    # Cleanup after test
    if os.path.exists(path):
        os.remove(path)

@pytest.mark.asyncio
async def test_classify_risk_low(analyzer):
    level, reason = analyzer.classify_risk("ls -la")
    assert level == "low"
    assert "Standard command" in reason

@pytest.mark.asyncio
async def test_classify_risk_high(analyzer):
    level, reason = analyzer.classify_risk("rm -rf folder")
    assert level == "high"
    assert "Forceful deletion" in reason

@pytest.mark.asyncio
async def test_classify_risk_critical(analyzer):
    level, reason = analyzer.classify_risk("rm -rf /")
    assert level == "critical"
    assert "Root directory deletion" in reason

@pytest.mark.asyncio
async def test_classify_risk_dd(analyzer):
    level, reason = analyzer.classify_risk("dd if=/dev/zero of=/dev/sda")
    assert level == "critical"
    assert "Raw device write" in reason

@pytest.mark.asyncio
async def test_log_command(analyzer):
    await analyzer.log_command("ls", "success")
    stats = await analyzer.get_behavioral_stats()
    assert "Total Operations: 1" in stats

@pytest.mark.asyncio
async def test_get_last_command(analyzer):
    assert await analyzer.get_last_command() is None
    await analyzer.log_command("pacman -Syu", "success")
    assert await analyzer.get_last_command() == "pacman -Syu"
    await analyzer.log_command("echo test", "success")
    assert await analyzer.get_last_command() == "echo test"

