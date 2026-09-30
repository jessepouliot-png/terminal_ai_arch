import os
import pytest
from unittest.mock import AsyncMock, MagicMock
from image_generator import ImageGenerator

def test_image_generator_init():
    gen = ImageGenerator()
    assert os.path.exists(gen.output_dir)

def test_sanitize_filename():
    gen = ImageGenerator()
    filename = gen._sanitize_filename("Neon Cyberpunk Arch Linux Logo!! @#$", ext=".png")
    assert filename.endswith(".png")
    assert "neon_cyberpunk_arch_linux_logo" in filename
    assert "!" not in filename
    assert "@" not in filename

@pytest.mark.asyncio
async def test_generate_image_empty_prompt():
    gen = ImageGenerator()
    res = await gen.generate_image("   ")
    assert res is None

@pytest.mark.asyncio
async def test_generate_image_mock_success(tmp_path):
    mock_client = MagicMock()
    mock_part = MagicMock()
    mock_part.inline_data.data = b"fake_png_data"
    mock_part.inline_data.mime_type = "image/png"
    mock_part.text = "Generated image description"

    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]

    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]

    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    gen = ImageGenerator(model=mock_client)
    saved_path = await gen.generate_image("test logo", output_dir=str(tmp_path))

    assert saved_path is not None
    assert os.path.exists(saved_path)
    with open(saved_path, "rb") as f:
        assert f.read() == b"fake_png_data"
