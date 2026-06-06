"""
Motor de Extração UDA — converte PDFs em dados estruturados via LLM.

Estratégia: Chunking Semântico por Títulos
  1. PyMuPDF extrai o texto página a página.
  2. Páginas são agrupadas em chunks pelos títulos detectados (seções).
  3. Apenas os chunks que contenham palavras-chave operacionais/financeiras
     são enviados ao LLM, reduzindo custo de tokens.
  4. O LLM retorna JSON validado pelo contrato Pydantic (RespostaExtracao).
  5. Resultados são persistidos no catálogo com linhagem completa.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
import httpx
from anthropic import Anthropic
from dotenv import load_dotenv

from src.catalog import (
    compute_hash,
    documento_ja_processado,
    marcar_processado,
    registrar_documento,
    salvar_dados_extraidos,
)
from src.models import RespostaExtracao

load_dotenv()

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

# Palavras-chave que indicam conteúdo relevante para extração
_KEYWORDS = re.compile(
    r"(lançamento|vendas?|contrat|vso|estoque|landbank|entrega|"
    r"receita|ebitda|lucro|margem|dívida|resultado|prévia|operacional|"
    r"trimestre|unidades?|vgv|habitacional|incorporadora)",
    re.IGNORECASE,
)

_SYSTEM_PROMPT = """Você é um analista especializado em dados do setor habitacional brasileiro.

Sua tarefa é extrair métricas operacionais e financeiras de relatórios trimestrais e prévias
operacionais de construtoras/incorporadoras.

REGRAS CRÍTICAS — NUNCA as viole:
1. Extraia APENAS valores absolutos (R$ mil, unidades). Ignore completamente as variações
   percentuais (YoY, QoQ, %) usadas pelo marketing para destacar resultados.
2. Se um campo não constar no documento, retorne null — JAMAIS invente ou estime valores.
3. Converta todos os valores monetários para R$ mil. Se o documento usar R$ milhões,
   multiplique por 1.000. Se usar R$ bilhões, multiplique por 1.000.000.
4. Identifique corretamente o trimestre e ano do período de referência (não da publicação).
5. Retorne um JSON estritamente conforme o schema fornecido.
"""


def _extract_text_from_pdf(pdf_bytes: bytes) -> list[tuple[int, str]]:
    """Retorna lista de (numero_pagina, texto) para cada página do PDF."""
    pages: list[tuple[int, str]] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page_num, page in enumerate(doc, start=1):
            text = page.get_text("text")
            if text.strip():
                pages.append((page_num, text))
    return pages


def _build_chunks(pages: list[tuple[int, str]], max_chars: int = 12_000) -> list[str]:
    """
    Agrupa páginas em chunks semânticos de até max_chars caracteres.
    Chunks que não contêm palavras-chave relevantes são descartados.
    """
    chunks: list[str] = []
    current = ""

    for page_num, text in pages:
        block = f"\n--- Página {page_num} ---\n{text}"
        if len(current) + len(block) > max_chars and current:
            if _KEYWORDS.search(current):
                chunks.append(current)
            current = block
        else:
            current += block

    if current and _KEYWORDS.search(current):
        chunks.append(current)

    return chunks


def _call_llm(client: Anthropic, chunk: str) -> Optional[RespostaExtracao]:
    """Envia um chunk ao Claude e retorna os dados validados pelo contrato Pydantic."""
    schema = RespostaExtracao.model_json_schema()

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Extraia os dados do trecho abaixo conforme o schema JSON.\n\n"
                    f"Schema esperado:\n{schema}\n\n"
                    f"Trecho do documento:\n{chunk}\n\n"
                    f"Responda SOMENTE com o JSON válido, sem markdown, sem explicações."
                ),
            }
        ],
    )

    raw = response.content[0].text.strip()
    # Remove blocos de código markdown caso o modelo os inclua
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    try:
        return RespostaExtracao.model_validate_json(raw)
    except Exception as exc:
        print(f"[extractor] Falha na validação do JSON: {exc}\nResposta bruta:\n{raw[:500]}")
        return None


def _merge_resultados(resultados: list[RespostaExtracao]) -> RespostaExtracao:
    """Consolida resultados de múltiplos chunks, mesclando dados da mesma empresa+período."""
    merged: dict[tuple[str, int, int], RespostaExtracao.model_fields["empresas"].annotation[0]] = {}

    for res in resultados:
        for emp in res.empresas:
            key = (emp.empresa.upper(), emp.ano, emp.trimestre)
            if key not in merged:
                merged[key] = emp
            else:
                existing = merged[key]
                op_e = existing.metricas_operacionais
                op_n = emp.metricas_operacionais
                fin_e = existing.metricas_financeiras
                fin_n = emp.metricas_financeiras

                # Prioriza valor não-nulo; mantém o existente em caso de conflito
                for field in op_e.model_fields:
                    if getattr(op_e, field) is None and getattr(op_n, field) is not None:
                        setattr(op_e, field, getattr(op_n, field))
                for field in fin_e.model_fields:
                    if getattr(fin_e, field) is None and getattr(fin_n, field) is not None:
                        setattr(fin_e, field, getattr(fin_n, field))

    obs_parts = [r.observacoes for r in resultados if r.observacoes]
    return RespostaExtracao(
        empresas=list(merged.values()),
        observacoes="; ".join(obs_parts) if obs_parts else None,
    )


def processar_pdf_bytes(
    pdf_bytes: bytes,
    empresa: str,
    url: str,
    nome_arquivo: Optional[str] = None,
) -> RespostaExtracao:
    """
    Pipeline completo: hash → idempotência → chunking → LLM → validação → catálogo.
    Retorna os dados extraídos mesmo que já existam no catálogo (idempotente).
    """
    hash_doc = compute_hash(pdf_bytes)

    if documento_ja_processado(hash_doc):
        print(f"[extractor] Documento já processado (hash={hash_doc[:12]}…). Pulando.")
        return RespostaExtracao(empresas=[], observacoes="Documento já processado anteriormente.")

    documento_id = registrar_documento(empresa, url, hash_doc, nome_arquivo)
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    pages = _extract_text_from_pdf(pdf_bytes)
    chunks = _build_chunks(pages)

    if not chunks:
        print("[extractor] Nenhum chunk relevante encontrado no documento.")
        marcar_processado(documento_id)
        return RespostaExtracao(empresas=[], observacoes="Nenhum conteúdo relevante detectado.")

    print(f"[extractor] Processando {len(chunks)} chunk(s) com o LLM…")
    resultados: list[RespostaExtracao] = []
    for i, chunk in enumerate(chunks, start=1):
        print(f"[extractor]  → Chunk {i}/{len(chunks)}")
        result = _call_llm(client, chunk)
        if result and result.empresas:
            resultados.append(result)

    if not resultados:
        marcar_processado(documento_id)
        return RespostaExtracao(empresas=[], observacoes="LLM não retornou dados estruturados.")

    final = _merge_resultados(resultados)

    for dados_empresa in final.empresas:
        salvar_dados_extraidos(
            documento_id=documento_id,
            fonte_url=url,
            hash_documento=hash_doc,
            dados_empresa=dados_empresa,
            observacoes=final.observacoes,
        )

    marcar_processado(documento_id)
    print(f"[extractor] Extração concluída: {len(final.empresas)} empresa(s) salva(s).")
    return final


def processar_pdf_url(empresa: str, url: str) -> RespostaExtracao:
    """Baixa o PDF da URL e executa o pipeline completo."""
    print(f"[extractor] Baixando PDF: {url}")
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        response = client.get(url)
        response.raise_for_status()
    nome = Path(url.split("?")[0]).name or "documento.pdf"
    return processar_pdf_bytes(response.content, empresa, url, nome)


def processar_pdf_local(empresa: str, caminho: str) -> RespostaExtracao:
    """Lê um arquivo PDF local e executa o pipeline completo."""
    path = Path(caminho)
    pdf_bytes = path.read_bytes()
    url = f"file://{path.resolve()}"
    return processar_pdf_bytes(pdf_bytes, empresa, url, path.name)
