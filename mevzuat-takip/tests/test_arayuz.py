"""Depodaki derlenmiş arayüz (frontend/dist) kaynakla uyumlu mu?

Sunucuda Node kurulmaz; panel depodaki dist/'i sunar. Biri frontend/src'yi değiştirip `npm run build`'i unutursa
sunucuya eski arayüz gider. Vite derlerken kaynak özetini dist/kaynak-ozeti.txt'ye yazar (frontend/vite.config.ts);
burada aynı özet yeniden hesaplanır.
"""

import hashlib
import re
from pathlib import Path

FRONTEND = Path(__file__).parents[1] / "frontend"
DIST = FRONTEND / "dist"
# frontend/vite.config.ts'teki KAYNAKLAR ile aynı olmalı.
KAYNAKLAR = ["src", "public", "index.html", "package.json", "package-lock.json", "vite.config.ts",
             "tsconfig.json", "tsconfig.app.json", "tsconfig.node.json"]


def kaynak_ozeti() -> str:
    dosyalar = []
    for k in KAYNAKLAR:
        yol = FRONTEND / k
        dosyalar += [p for p in yol.rglob("*") if p.is_file()] if yol.is_dir() else [yol]
    ozet = hashlib.sha256()
    for ad in sorted(p.relative_to(FRONTEND).as_posix() for p in dosyalar):
        ozet.update(ad.encode() + b"\0")
        ozet.update((FRONTEND / ad).read_bytes().replace(b"\r", b""))  # CRLF/LF farkı özeti değiştirmesin
        ozet.update(b"\0")
    return ozet.hexdigest()


def test_derlenmis_arayuz_guncel():
    yazili = (DIST / "kaynak-ozeti.txt").read_text(encoding="utf-8").strip()
    assert yazili == kaynak_ozeti(), (
        "frontend/ kaynağı değişmiş ama dist/ yeniden derlenmemiş: cd frontend && npm run build"
    )


def test_derlenmis_arayuz_csp_ile_uyumlu():
    """Panelin CSP'si satır içi script/stil ve dış kaynak kabul etmez; derleme buna uymalı."""
    html = (DIST / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)", html), "satır içi script var"
    assert "<style" not in html and "http://" not in html and "https://" not in html
    for yol in re.findall(r'(?:src|href)="(/[^"]+)"', html):
        assert (DIST / yol.lstrip("/")).is_file(), f"index.html'in işaret ettiği dosya yok: {yol}"
