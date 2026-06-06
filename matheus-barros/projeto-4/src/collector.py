"""
Coletor de Portais RI — monitora as Centrais de Resultados das incorporadoras
via polling agendado e dispara a extração quando detecta novos PDFs.

Estratégia de Ingestão: Polling/CronJob
  - Um scheduler APScheduler executa o job a cada POLLING_INTERVAL_HOURS horas.
  - Cada crawler visita a página RI de uma empresa e coleta links de PDF.
  - Antes de processar, calcula o hash SHA-256 da URL (proxy para idempotência
    quando o arquivo ainda não foi baixado) e verifica no catálogo.
  - Se o hash for novo, baixa o PDF e aciona o pipeline de extração.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from src.catalog import compute_hash, documento_ja_processado
from src.extractor import processar_pdf_url

load_dotenv()

POLLING_INTERVAL_HOURS = int(os.getenv("POLLING_INTERVAL_HOURS", "24"))


@dataclass
class FonteRI:
    """Configuração de uma fonte de dados de uma construtora."""

    empresa: str
    url_ri: str
    # Termos que identificam documentos de prévia operacional na listagem da página
    palavras_chave_doc: list[str] = field(
        default_factory=lambda: [
            "prévia operacional",
            "previa operacional",
            "resultado",
            "release",
            "earnings",
            "trimestral",
        ]
    )


# Fontes configuradas — adicione/edite conforme necessário
FONTES: list[FonteRI] = [
    FonteRI(
        empresa="MRV",
        url_ri="https://ri.mrv.com.br/central-de-resultados",
        palavras_chave_doc=["prévia operacional", "resultado", "release"],
    ),
    FonteRI(
        empresa="Direcional",
        url_ri="https://ri.direcional.com.br/central-de-resultados",
        palavras_chave_doc=["prévia operacional", "resultado"],
    ),
    FonteRI(
        empresa="Tenda",
        url_ri="https://ri.construtoratendam.com.br/central-de-resultados",
        palavras_chave_doc=["prévia operacional", "resultado"],
    ),
    FonteRI(
        empresa="Cury",
        url_ri="https://ri.cury.com.br/central-de-resultados",
        palavras_chave_doc=["prévia operacional", "resultado"],
    ),
    FonteRI(
        empresa="Plano&Plano",
        url_ri="https://ri.planoplano.com.br/central-de-resultados",
        palavras_chave_doc=["prévia operacional", "resultado"],
    ),
    FonteRI(
        empresa="Pacaembu",
        url_ri="https://ri.pacaembu.com/central-de-resultados",
        palavras_chave_doc=["prévia operacional", "resultado"],
    ),
]

_PDF_PATTERN = re.compile(r"\.pdf(\?.*)?$", re.IGNORECASE)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; UDA-Pipeline/1.0; "
        "+https://github.com/unb-Sistemas-de-Machine-learning)"
    )
}


def _coletar_links_pdf(fonte: FonteRI) -> list[str]:
    """Visita a página de RI e retorna URLs de PDFs candidatos."""
    try:
        with httpx.Client(headers=_HEADERS, follow_redirects=True, timeout=30) as client:
            resp = client.get(fonte.url_ri)
            resp.raise_for_status()
    except Exception as exc:
        print(f"[collector] Erro ao acessar {fonte.empresa} ({fonte.url_ri}): {exc}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    links: list[str] = []

    for tag in soup.find_all("a", href=True):
        href: str = tag["href"]
        texto: str = tag.get_text(" ", strip=True).lower()

        is_pdf = _PDF_PATTERN.search(href)
        has_keyword = any(kw.lower() in texto for kw in fonte.palavras_chave_doc)

        if is_pdf or has_keyword:
            # Normaliza URL relativa para absoluta
            if href.startswith("http"):
                url = href
            else:
                base = "/".join(fonte.url_ri.split("/")[:3])
                url = base + "/" + href.lstrip("/")
            links.append(url)

    return list(dict.fromkeys(links))  # deduplica mantendo ordem


def _processar_fonte(fonte: FonteRI) -> None:
    print(f"[collector] Verificando {fonte.empresa}…")
    links = _coletar_links_pdf(fonte)

    if not links:
        print(f"[collector]   Nenhum link encontrado em {fonte.empresa}.")
        return

    novos = 0
    for url in links:
        hash_url = compute_hash(url.encode())
        if documento_ja_processado(hash_url):
            continue
        print(f"[collector]   Novo PDF detectado: {url}")
        try:
            resultado = processar_pdf_url(fonte.empresa, url)
            novos += 1
            print(f"[collector]   → {len(resultado.empresas)} empresa(s) extraída(s).")
        except Exception as exc:
            print(f"[collector]   Erro ao processar {url}: {exc}")

    if novos == 0:
        print(f"[collector]   Nenhum documento novo em {fonte.empresa}.")


def executar_coleta_manual(empresa: Optional[str] = None) -> None:
    """Dispara a coleta imediatamente (útil para testes e execução pontual)."""
    fontes = [f for f in FONTES if empresa is None or f.empresa.lower() == empresa.lower()]
    for fonte in fontes:
        _processar_fonte(fonte)


def iniciar_scheduler() -> BackgroundScheduler:
    """Inicia o scheduler que executa a coleta periodicamente."""
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=executar_coleta_manual,
        trigger="interval",
        hours=POLLING_INTERVAL_HOURS,
        id="coleta_ri",
        name="Coleta RI das Incorporadoras",
        replace_existing=True,
    )
    scheduler.start()
    print(
        f"[collector] Scheduler iniciado — intervalo: {POLLING_INTERVAL_HOURS}h. "
        f"Fontes monitoradas: {[f.empresa for f in FONTES]}"
    )
    return scheduler
