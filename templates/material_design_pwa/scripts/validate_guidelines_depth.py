#!/usr/bin/env python3
"""Gate rigoroso de profundidade M3: verifica que a UI usa os PADRÕES de componente
das guidelines (não só tokens/nav/PWA da fundação), via marcadores estáveis data-md-*.

Contrato de marcadores que o ciclo DEVE produzir (análogo a data-ui-criteria):
  data-md-component="list"        no container de lista de conteúdo (compras, garantias, preços, busca)
  data-md-component="list-item"   em cada item de lista, com um elemento LEADING (ícone/avatar)
  data-md-component="fab"         na ação principal inequívoca (ex.: adicionar/capturar compra)
  data-md-component="chip"        nos filtros da linha do tempo (filter chips)
  data-md-component="search"      na barra de busca de /busca
  .m3-state-layer / state-layer   utilitário de state layer/ripple para feedback de toque

Uso: validate_guidelines_depth.py [project_root]
Sai 0 se todos os checks passam; !=0 listando os que faltam.
"""
import re
import sys
from pathlib import Path

SRC = "project/frontend/src"


def read(root: Path, rel: str) -> str:
    p = root / rel
    return p.read_text(encoding="utf-8", errors="ignore") if p.is_file() else ""


def all_src(root: Path) -> str:
    base = root / SRC
    if not base.is_dir():
        return ""
    return "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in base.rglob("*")
        if p.is_file() and p.suffix in (".tsx", ".ts", ".css", ".jsx", ".js")
    )


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    src = all_src(root)
    fails: list[str] = []

    # 1. Listas de conteúdo com marcador de list-item E elemento leading (ícone)
    if 'data-md-component="list-item"' not in src:
        fails.append(
            "LIST-ITEM: nenhum item de lista com data-md-component=\"list-item\" — "
            "listas de conteúdo (compras/garantias/preços/busca) devem usar list items M3 "
            "com elemento leading (ícone/avatar), não <li> cru com link default"
        )
    else:
        # TODO arquivo com list-item deve ter um leading (ícone/avatar) — não só um.
        li_files = [
            p for p in (root / SRC).rglob("*.tsx")
            if 'data-md-component="list-item"' in p.read_text(errors="ignore")
        ]
        sem_leading = [
            str(p.relative_to(root))
            for p in li_files
            if not re.search(r'data-md-(leading|icon)|Icon\w*|<svg', p.read_text(errors="ignore"))
        ]
        if sem_leading:
            fails.append(
                "LIST-ITEM-LEADING: list items sem elemento leading (ícone/avatar) em: "
                + ", ".join(sorted(sem_leading))
            )

    # 2. Lista de conteúdo sem bullets default (list-style: none nas listas M3)
    if 'data-md-component="list"' not in src:
        fails.append('LIST: nenhum container data-md-component="list"')

    # 2b. Cobertura POR-ROTA: nenhuma page/componente pode ter <li> cru sem
    #     marcador M3 no mesmo arquivo nem usar um componente de list-item.
    #     (Presença global de marcador não garante que TODA lista foi migrada.)
    app_dir = root / SRC / "app"
    comp_dir = root / SRC / "components"
    raw_li_files = []
    for base in (app_dir, comp_dir):
        if not base.is_dir():
            continue
        for p in base.rglob("*.tsx"):
            txt = p.read_text(errors="ignore")
            if "MainNav" in p.name or "Nav" in p.name:
                continue  # a navegação não é lista de conteúdo
            if re.search(r"<li[\s>]", txt):
                usa_marcador = "data-md-component" in txt
                usa_componente = re.search(r"import[^\n]*(ListItem|PurchaseListItem)", txt)
                if not usa_marcador and not usa_componente:
                    raw_li_files.append(str(p.relative_to(root)))
    if raw_li_files:
        fails.append(
            "LIST-COVERAGE: rotas com <li> cru sem componente/atributo M3 (migrar cada uma): "
            + ", ".join(sorted(raw_li_files))
        )

    # 3. Links de item de lista não podem usar cor default do browser (azul sublinhado)
    #    — deve haver uma regra de reset de link nas listas M3
    css = read(root, f"{SRC}/app/globals.css") + read(root, f"{SRC}/app/theme.css")
    if not re.search(r'data-md-component="list"[^{]*\{[^}]*list-style\s*:\s*none', css, re.S) \
       and 'list-style: none' not in css and 'list-style:none' not in css:
        fails.append("LIST-STYLE: listas M3 devem remover bullets default (list-style: none)")

    # 4. FAB persistente e SENSÍVEL AO CONTEXTO (rota/aba) em todas as telas
    fab_files = [
        p for p in (root / SRC).rglob("*.tsx")
        if 'data-md-component="fab"' in p.read_text(errors="ignore")
    ]
    if not fab_files:
        fails.append('FAB: falta um FAB (data-md-component="fab")')
    else:
        # (a) sensível ao contexto: varia a ação por rota (usePathname)
        contexto = any("usePathname" in p.read_text(errors="ignore") for p in fab_files)
        if not contexto:
            fails.append(
                "FAB-CONTEXTO: o FAB deve ser sensível ao contexto — usar usePathname "
                "para variar a ação por rota (QR na captura, adicionar garantia em garantias, etc.)"
            )
        # (b) persistente: renderizado pelo app shell/layout, não numa página só
        layout = read(root, f"{SRC}/app/layout.tsx")
        fab_no_shell = ('data-md-component="fab"' in layout) or bool(re.search(r"\bFab\b|<\w*Fab", layout))
        if not fab_no_shell:
            fails.append(
                "FAB-PERSISTENTE: o FAB deve ser renderizado pelo app shell (app/layout.tsx), "
                "para aparecer em TODAS as telas — não apenas numa página"
            )

    # 5. Filtros da timeline como chips
    if 'data-md-component="chip"' not in src:
        fails.append('CHIP: filtros da linha do tempo devem usar filter chips (data-md-component="chip")')

    # 6. Barra de busca M3
    if 'data-md-component="search"' not in src:
        fails.append('SEARCH: /busca deve usar uma barra de busca M3 (data-md-component="search")')

    # 7. State layer / ripple
    if not re.search(r'state-layer|m3-state|\.ripple', src):
        fails.append("STATE-LAYER: falta utilitário de state layer/ripple para feedback de toque")

    if fails:
        print("guidelines-depth FAIL: " + str(len(fails)) + " requisito(s) de componente M3 não atendidos:")
        for f in fails:
            print("  - " + f)
        return 1
    print("guidelines-depth PASS: componentes M3 (lista/leading/FAB/chip/search/state-layer) presentes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
