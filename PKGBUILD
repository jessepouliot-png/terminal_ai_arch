# Maintainer: Arch AI Terminal <arch-ai@localhost>
pkgname=arch-ai-terminal
pkgver=1.0.0
pkgrel=1
pkgdesc="Production-grade AI Terminal for Arch Linux with Docker Sandbox, Steam & ProtonDB, and Gemini Tool Calling"
arch=('any')
license=('MIT')
depends=(
    'python'
    'python-prompt_toolkit'
    'python-rich'
    'python-psutil'
    'python-httpx'
    'python-dotenv'
    'python-tenacity'
    'python-aiosqlite'
    'docker'
)
optdepends=(
    'gamemode: Recommended for Arch Linux gaming optimizations'
    'mangohud: Recommended for gaming HUD and FPS overlays'
    'chafa: For inline terminal thumbnail graphics in /image mode'
    'xdg-utils: For launching default image viewer via xdg-open'
)
makedepends=('python-build' 'python-installer' 'python-setuptools' 'python-wheel')

build() {
    python -m build --wheel --no-isolation
}

package() {
    python -m installer --destdir="$pkgdir" dist/*.whl
}
