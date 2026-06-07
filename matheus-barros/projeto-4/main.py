"""
Entry point do Pipeline UDA — Setor Habitacional.

Modos de uso:
  python main.py serve          → sobe a API REST + scheduler de polling
  python main.py extract <url>  → extrai PDF a partir de uma URL (empresa inferida da URL)
  python main.py extract-local <empresa> <caminho>  → extrai PDF local
  python main.py collect [empresa]  → coleta manual nos portais RI
"""

from __future__ import annotations

import sys
import os

# Console do Windows usa cp1252 por padrão e quebra com acentos/setas em print()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv()

from src.catalog import init_db


def cmd_serve() -> None:
    import uvicorn
    from src.collector import iniciar_scheduler
    from src.api import app

    init_db()
    scheduler = iniciar_scheduler()

    try:
        port = int(os.getenv("API_PORT", "8000"))
        print(f"[main] API disponível em http://localhost:{port}/docs")
        uvicorn.run(app, host="0.0.0.0", port=port)
    finally:
        scheduler.shutdown()


def cmd_extract(url: str) -> None:
    from src.extractor import processar_pdf_url

    init_db()
    empresa = _infer_empresa(url)
    print(f"[main] Extraindo PDF: {url} (empresa: {empresa})")
    resultado = processar_pdf_url(empresa, url)
    _print_resultado(resultado)


def cmd_extract_local(empresa: str, caminho: str) -> None:
    from src.extractor import processar_pdf_local

    init_db()
    print(f"[main] Extraindo arquivo local: {caminho} (empresa: {empresa})")
    resultado = processar_pdf_local(empresa, caminho)
    _print_resultado(resultado)


def cmd_collect(empresa: str | None = None) -> None:
    from src.collector import executar_coleta_manual

    init_db()
    executar_coleta_manual(empresa)


def _infer_empresa(url: str) -> str:
    mapa = {
        "mrv": "MRV",
        "direcional": "Direcional",
        "tenda": "Tenda",
        "cury": "Cury",
        "plano": "Plano&Plano",
        "pacaembu": "Pacaembu",
    }
    url_lower = url.lower()
    for key, nome in mapa.items():
        if key in url_lower:
            return nome
    return "Desconhecida"


def _print_resultado(resultado) -> None:
    if not resultado.empresas:
        print("[main] Nenhum dado extraído.")
        return
    for emp in resultado.empresas:
        print(f"\n{'='*50}")
        print(f"Empresa: {emp.empresa} | {emp.trimestre}T{emp.ano} | {emp.tipo_relatorio}")
        op = emp.metricas_operacionais
        fin = emp.metricas_financeiras
        print(f"  Unidades lançadas:    {op.unidades_lancadas}")
        print(f"  Vendas contratadas:   R$ {op.vendas_contratadas_mil_reais} mil")
        print(f"  Unidades vendidas:    {op.unidades_vendidas}")
        print(f"  VSO:                  {op.vso_percentual}%")
        print(f"  Estoque:              {op.estoque_unidades} unidades")
        print(f"  Receita líquida:      R$ {fin.receita_liquida_mil_reais} mil")
        print(f"  EBITDA ajustado:      R$ {fin.ebitda_ajustado_mil_reais} mil")
        print(f"  Margem EBITDA:        {fin.margem_ebitda_percentual}%")
        print(f"  Lucro líquido:        R$ {fin.lucro_liquido_mil_reais} mil")
    if resultado.observacoes:
        print(f"\nObservações: {resultado.observacoes}")


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] == "serve":
        cmd_serve()
    elif args[0] == "extract" and len(args) >= 2:
        cmd_extract(args[1])
    elif args[0] == "extract-local" and len(args) >= 3:
        cmd_extract_local(args[1], args[2])
    elif args[0] == "collect":
        cmd_collect(args[1] if len(args) > 1 else None)
    else:
        print(__doc__)
        sys.exit(1)
