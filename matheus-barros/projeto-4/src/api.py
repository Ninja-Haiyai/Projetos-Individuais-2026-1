"""
Camada de Serviço — API REST com FastAPI.

Endpoints principais:
  GET  /api/conjuntura          → filtra dados extraídos por empresa/ano/trimestre
  GET  /api/empresas            → lista empresas disponíveis no banco
  GET  /api/documentos          → catálogo com linhagem dos PDFs processados
  POST /api/processar/url       → ingestão manual de PDF por URL
  POST /api/processar/local     → ingestão manual de PDF por caminho local
  POST /api/coletar             → dispara coleta imediata nos portais RI
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.catalog import buscar_dados, listar_documentos
from src.collector import executar_coleta_manual
from src.extractor import processar_pdf_local, processar_pdf_url

app = FastAPI(
    title="UDA Pipeline — Setor Habitacional",
    description=(
        "Pipeline de análise de dados não estruturados (UDA) para o "
        "Relatório de Conjuntura do Setor Habitacional — Ministério das Cidades."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Schemas de resposta ────────────────────────────────────────────────────────

class MetricasOperacionaisResp(BaseModel):
    unidades_lancadas: Optional[int]
    vgv_lancamentos_mil_reais: Optional[float]
    unidades_vendidas: Optional[int]
    vendas_contratadas_mil_reais: Optional[float]
    vso_percentual: Optional[float]
    estoque_unidades: Optional[int]
    estoque_vgv_mil_reais: Optional[float]
    landbank_unidades: Optional[int]
    entregas_unidades: Optional[int]


class MetricasFinanceirasResp(BaseModel):
    receita_liquida_mil_reais: Optional[float]
    lucro_bruto_mil_reais: Optional[float]
    margem_bruta_percentual: Optional[float]
    ebitda_ajustado_mil_reais: Optional[float]
    margem_ebitda_percentual: Optional[float]
    lucro_liquido_mil_reais: Optional[float]
    margem_liquida_percentual: Optional[float]
    divida_liquida_mil_reais: Optional[float]


class ConjunturaResp(BaseModel):
    id: int
    empresa: str
    ano: int
    trimestre: int
    tipo_relatorio: str
    metricas_operacionais: MetricasOperacionaisResp
    metricas_financeiras: MetricasFinanceirasResp
    linhagem: dict


class DocumentoResp(BaseModel):
    id: int
    empresa: str
    url: str
    hash_sha256: str
    nome_arquivo: Optional[str]
    data_coleta: datetime
    processado: bool


class ProcessarURLReq(BaseModel):
    empresa: str
    url: str


class ProcessarLocalReq(BaseModel):
    empresa: str
    caminho: str


class ColetarReq(BaseModel):
    empresa: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/conjuntura", response_model=list[ConjunturaResp], tags=["Dados"])
def get_conjuntura(
    empresa: Optional[str] = Query(None, description="Nome da empresa (parcial, case-insensitive)"),
    ano: Optional[int] = Query(None, description="Ano de referência (ex: 2025)"),
    trimestre: Optional[int] = Query(None, ge=1, le=4, description="Trimestre (1-4)"),
):
    """
    Retorna os dados extraídos das prévias operacionais e resultados trimestrais.

    Exemplos:
    - `/api/conjuntura?empresa=MRV&ano=2025&trimestre=3`
    - `/api/conjuntura?ano=2025`
    """
    rows = buscar_dados(empresa=empresa, ano=ano, trimestre=trimestre)
    return [
        ConjunturaResp(
            id=r.id,
            empresa=r.empresa,
            ano=r.ano,
            trimestre=r.trimestre,
            tipo_relatorio=r.tipo_relatorio,
            metricas_operacionais=MetricasOperacionaisResp(
                unidades_lancadas=r.unidades_lancadas,
                vgv_lancamentos_mil_reais=r.vgv_lancamentos_mil_reais,
                unidades_vendidas=r.unidades_vendidas,
                vendas_contratadas_mil_reais=r.vendas_contratadas_mil_reais,
                vso_percentual=r.vso_percentual,
                estoque_unidades=r.estoque_unidades,
                estoque_vgv_mil_reais=r.estoque_vgv_mil_reais,
                landbank_unidades=r.landbank_unidades,
                entregas_unidades=r.entregas_unidades,
            ),
            metricas_financeiras=MetricasFinanceirasResp(
                receita_liquida_mil_reais=r.receita_liquida_mil_reais,
                lucro_bruto_mil_reais=r.lucro_bruto_mil_reais,
                margem_bruta_percentual=r.margem_bruta_percentual,
                ebitda_ajustado_mil_reais=r.ebitda_ajustado_mil_reais,
                margem_ebitda_percentual=r.margem_ebitda_percentual,
                lucro_liquido_mil_reais=r.lucro_liquido_mil_reais,
                margem_liquida_percentual=r.margem_liquida_percentual,
                divida_liquida_mil_reais=r.divida_liquida_mil_reais,
            ),
            linhagem={
                "fonte_pdf_url": r.fonte_pdf_url,
                "hash_documento": r.hash_documento,
                "data_processamento": r.data_processamento.isoformat() if r.data_processamento else None,
                "observacoes": r.observacoes_extracao,
            },
        )
        for r in rows
    ]


@app.get("/api/empresas", response_model=list[str], tags=["Dados"])
def get_empresas():
    """Lista todas as empresas disponíveis no banco de dados."""
    rows = buscar_dados()
    empresas = sorted({r.empresa for r in rows})
    return empresas


@app.get("/api/documentos", response_model=list[DocumentoResp], tags=["Catálogo"])
def get_documentos():
    """Lista todos os documentos no catálogo com informações de linhagem."""
    docs = listar_documentos()
    return [
        DocumentoResp(
            id=d.id,
            empresa=d.empresa,
            url=d.url,
            hash_sha256=d.hash_sha256,
            nome_arquivo=d.nome_arquivo,
            data_coleta=d.data_coleta,
            processado=d.processado,
        )
        for d in docs
    ]


@app.post("/api/processar/url", tags=["Ingestão"])
def post_processar_url(req: ProcessarURLReq):
    """
    Ingestão manual: baixa o PDF da URL informada e executa o pipeline de extração.
    Idempotente — documentos já processados são ignorados.
    """
    try:
        resultado = processar_pdf_url(req.empresa, req.url)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {
        "empresas_extraidas": len(resultado.empresas),
        "empresas": [e.empresa for e in resultado.empresas],
        "observacoes": resultado.observacoes,
    }


@app.post("/api/processar/local", tags=["Ingestão"])
def post_processar_local(req: ProcessarLocalReq):
    """
    Ingestão manual: processa um arquivo PDF local pelo caminho informado.
    Útil para testes e carga inicial de dados históricos.
    """
    try:
        resultado = processar_pdf_local(req.empresa, req.caminho)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Arquivo não encontrado: {req.caminho}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {
        "empresas_extraidas": len(resultado.empresas),
        "empresas": [e.empresa for e in resultado.empresas],
        "observacoes": resultado.observacoes,
    }


@app.post("/api/coletar", tags=["Ingestão"])
def post_coletar(req: ColetarReq):
    """
    Dispara imediatamente uma rodada de coleta nos portais RI.
    Se `empresa` for informado, coleta apenas aquela empresa.
    """
    try:
        executar_coleta_manual(req.empresa)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"status": "coleta iniciada", "empresa": req.empresa or "todas"}


@app.get("/", tags=["Status"])
def root():
    return {
        "servico": "UDA Pipeline — Setor Habitacional",
        "status": "online",
        "docs": "/docs",
    }
