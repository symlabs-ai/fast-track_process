#!/usr/bin/env python3
"""Captura screenshots do PWA em múltiplos breakpoints e temas para o gate de guidelines.

Uso: capture_mdpwa_screenshots.py <base_url> [out_dir]
Boota nada — assume o frontend já servido em base_url. Salva PNGs em out_dir
(default docs/mdpwa-screenshots/) como <rota>__<breakpoint>__<tema>.png.

Breakpoints M3: compact (390), medium (840), expanded (1280).
Temas: light e dark (via emulateMedia prefers-color-scheme).
"""
import sys
from pathlib import Path

ROTAS = [
    ("home", "/"),
    ("timeline", "/timeline"),
    ("garantias", "/garantias"),
    ("busca", "/busca?q=arroz"),
    ("conta", "/conta"),
    ("captura-qrcode", "/captura/qrcode"),
]
BREAKPOINTS = [("compact", 390, 844), ("medium", 840, 1120), ("expanded", 1280, 900)]
TEMAS = ["light", "dark"]


def main() -> int:
    if len(sys.argv) < 2:
        print("uso: capture_mdpwa_screenshots.py <base_url> [out_dir]", file=sys.stderr)
        return 2
    base_url = sys.argv[1].rstrip("/")
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("docs/mdpwa-screenshots")
    out_dir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    total = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for tema in TEMAS:
            for bp_nome, w, h in BREAKPOINTS:
                ctx = browser.new_context(
                    viewport={"width": w, "height": h},
                    color_scheme=tema,
                    device_scale_factor=2,
                )
                page = ctx.new_page()
                for rota_nome, rota in ROTAS:
                    try:
                        page.goto(f"{base_url}{rota}", wait_until="networkidle", timeout=30000)
                        page.wait_for_timeout(600)
                        dest = out_dir / f"{rota_nome}__{bp_nome}__{tema}.png"
                        page.screenshot(path=str(dest), full_page=True)
                        total += 1
                    except Exception as e:
                        print(f"AVISO: falha em {rota} [{bp_nome}/{tema}]: {e}", file=sys.stderr)
                ctx.close()
        browser.close()

    print(f"{total} screenshots salvos em {out_dir}")
    if total < len(ROTAS) * len(BREAKPOINTS) * len(TEMAS) * 0.8:
        print("ERRO: menos de 80% das capturas concluídas", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
