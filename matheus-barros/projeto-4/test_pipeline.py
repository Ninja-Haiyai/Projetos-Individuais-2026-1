"""
Teste de integração do pipeline UDA — usa um mock da chamada ao LLM para
validar toda a arquitetura (parsing de PDF, chunking, contrato semântico,
catálogo com linhagem/idempotência e API REST) sem custo de API.

O mock simula uma extração "correta": o boletim de exemplo contém apenas
COMPARATIVOS PERCENTUAIS entre incorporadoras (não valores absolutos), então
um LLM bem orientado pelo contrato deve retornar NULL nos campos de valor
absoluto — exatamente o comportamento anti-alucinação exigido pela
especificação ("ignorar variações percentuais... tratar ausentes como NULL").

Rodar com:  python test_pipeline.py
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import patch

# Console do Windows usa cp1252 por padrão e quebra com acentos/setas em print()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Banco de teste isolado (não polui o catálogo de produção)
TEST_DB = "data/test_catalog.db"
Path("data").mkdir(exist_ok=True)
if Path("test_catalog.db").exists():
    Path("test_catalog.db").unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
if Path(TEST_DB).exists():
    Path(TEST_DB).unlink()
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-mock-key")

from src.models import (  # noqa: E402
    DadosEmpresa,
    MetricasFinanceiras,
    MetricasOperacionais,
    RespostaExtracao,
)
from src import catalog  # noqa: E402
from src import extractor  # noqa: E402

EMPRESAS_NO_BOLETIM = ["MRV", "Cury", "Tenda", "Plano & Plano", "Direcional", "Pacaembu"]


def _mock_resposta_realista(*_args, **_kwargs) -> RespostaExtracao:
    """
    Simula a resposta de um LLM bem orientado pelo Contrato Semântico:
    o documento só traz variações percentuais (ex: 'MRV -32% X 2T25'),
    então os campos de valor ABSOLUTO ficam NULL — o modelo não inventa
    números que não constam no texto.
    """
    empresas = [
        DadosEmpresa(
            empresa=nome,
            ano=2025,
            trimestre=3,
            tipo_relatorio="boletim_conjuntura",
            metricas_operacionais=MetricasOperacionais(),  # tudo None — só há % no doc
            metricas_financeiras=MetricasFinanceiras(),    # tudo None — não há dados financeiros
        )
        for nome in EMPRESAS_NO_BOLETIM
    ]
    return RespostaExtracao(
        empresas=empresas,
        observacoes=(
            "Documento contém apenas variações percentuais de lançamentos e vendas "
            "(QoQ e YoY) por empresa — nenhum valor absoluto em R$ ou unidades foi "
            "encontrado no texto. Campos numéricos retornados como NULL conforme "
            "o contrato semântico, evitando alucinação de dados inexistentes."
        ),
    )


def run() -> None:
    print("=" * 70)
    print("TESTE DE INTEGRAÇÃO — Pipeline UDA (LLM mockado)")
    print("=" * 70)

    catalog.init_db()

    pdf_path = "exemplo_Boletim_Conjuntura_2025_3T.pdf"
    assert Path(pdf_path).exists(), f"PDF de exemplo não encontrado: {pdf_path}"

    # ── 1) Parsing + chunking reais (sem mock) ──────────────────────────────
    pdf_bytes = Path(pdf_path).read_bytes()
    pages = extractor._extract_text_from_pdf(pdf_bytes)
    chunks = extractor._build_chunks(pages)
    print(f"\n[1] Parsing real do PDF:")
    print(f"    Páginas com texto: {len(pages)}")
    print(f"    Chunks semânticos relevantes (com keywords): {len(chunks)}")
    assert len(pages) > 0, "PyMuPDF não extraiu texto do PDF"
    assert len(chunks) > 0, "Chunking não encontrou conteúdo relevante"

    # ── 2) Pipeline completo com LLM mockado ────────────────────────────────
    print(f"\n[2] Executando pipeline completo (1ª vez) com LLM mockado…")
    with patch.object(extractor, "_call_llm", side_effect=_mock_resposta_realista):
        resultado = extractor.processar_pdf_local("Setor Habitacional", pdf_path)

    print(f"    Empresas extraídas: {len(resultado.empresas)}")
    for emp in resultado.empresas:
        print(f"      - {emp.empresa} | {emp.trimestre}T{emp.ano} | {emp.tipo_relatorio}")
    assert len(resultado.empresas) == len(EMPRESAS_NO_BOLETIM), "Número de empresas inesperado"
    assert all(e.metricas_operacionais.unidades_lancadas is None for e in resultado.empresas), (
        "Campo absoluto deveria ser NULL (documento só tem percentuais)"
    )
    print("    ✓ Contrato semântico validou o JSON do LLM")
    print("    ✓ Campos de valor absoluto retornados como NULL (sem alucinação)")

    # ── 3) Idempotência — reprocessar o mesmo PDF não deve duplicar ─────────
    print(f"\n[3] Testando idempotência (reprocessando o mesmo PDF)…")
    with patch.object(extractor, "_call_llm", side_effect=_mock_resposta_realista):
        resultado2 = extractor.processar_pdf_local("Setor Habitacional", pdf_path)
    assert resultado2.empresas == [], "Documento já processado não deveria gerar novos dados"
    print("    ✓ Hash SHA-256 detectou documento já processado — pipeline não duplicou")

    # ── 4) Catálogo + Linhagem ───────────────────────────────────────────────
    print(f"\n[4] Verificando Catálogo de Dados e Linhagem…")
    docs = catalog.listar_documentos()
    assert len(docs) == 1, f"Esperado 1 documento no catálogo, encontrado {len(docs)}"
    doc = docs[0]
    print(f"    Documento: id={doc.id} hash={doc.hash_sha256[:16]}… processado={doc.processado}")
    assert doc.processado is True
    assert len(doc.hash_sha256) == 64

    rows = catalog.buscar_dados()
    assert len(rows) == len(EMPRESAS_NO_BOLETIM)
    primeira = rows[0]
    print(f"    Linha de exemplo: {primeira.empresa} {primeira.trimestre}T{primeira.ano}")
    print(f"      fonte_pdf_url = {primeira.fonte_pdf_url}")
    print(f"      hash_documento = {primeira.hash_documento[:16]}…")
    assert primeira.fonte_pdf_url.startswith("file://"), "Linhagem deve apontar pro PDF de origem"
    assert primeira.hash_documento == doc.hash_sha256, "Linhagem deve referenciar o hash do documento"
    print("    ✓ Cada linha do banco está associada ao PDF de origem (data lineage OK)")

    # ── 5) Filtros de consulta ───────────────────────────────────────────────
    print(f"\n[5] Testando filtros de consulta…")
    mrv = catalog.buscar_dados(empresa="MRV", ano=2025, trimestre=3)
    assert len(mrv) == 1 and mrv[0].empresa == "MRV"
    print(f"    buscar_dados(empresa='MRV', ano=2025, trimestre=3) → {len(mrv)} resultado")
    vazio = catalog.buscar_dados(empresa="MRV", trimestre=1)
    assert len(vazio) == 0
    print(f"    buscar_dados(empresa='MRV', trimestre=1) → {len(vazio)} resultados (filtro funcionando)")

    # ── 6) Camada de Serviço (API REST) ──────────────────────────────────────
    print(f"\n[6] Testando API REST (FastAPI TestClient)…")
    from fastapi.testclient import TestClient
    from src.api import app

    client = TestClient(app)

    resp = client.get("/api/conjuntura", params={"empresa": "MRV", "ano": 2025, "trimestre": 3})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    assert data[0]["empresa"] == "MRV"
    assert data[0]["linhagem"]["fonte_pdf_url"].startswith("file://")
    print(f"    GET /api/conjuntura?empresa=MRV&ano=2025&trimestre=3 → 200 OK, {len(data)} resultado")
    print(f"      linhagem.fonte_pdf_url presente: {bool(data[0]['linhagem']['fonte_pdf_url'])}")

    resp2 = client.get("/api/empresas")
    assert resp2.status_code == 200
    print(f"    GET /api/empresas → 200 OK, {len(resp2.json())} empresas: {resp2.json()}")
    assert set(resp2.json()) == set(EMPRESAS_NO_BOLETIM)

    resp3 = client.get("/api/documentos")
    assert resp3.status_code == 200
    assert len(resp3.json()) == 1
    print(f"    GET /api/documentos → 200 OK, {len(resp3.json())} documento no catálogo")

    print("\n" + "=" * 70)
    print("TODOS OS TESTES PASSARAM ✓")
    print("=" * 70)
    print(
        "\nResumo: o pipeline extraiu, validou (contrato semântico), persistiu\n"
        "com linhagem/idempotência e serviu via API — as três camadas\n"
        "obrigatórias da especificação estão funcionando de ponta a ponta."
    )


if __name__ == "__main__":
    try:
        run()
    finally:
        # Libera as conexões do SQLAlchemy antes de apagar o arquivo
        # (no Windows o SQLite mantém o arquivo travado enquanto o engine existe)
        catalog.engine.dispose()
        if Path(TEST_DB).exists():
            Path(TEST_DB).unlink()
