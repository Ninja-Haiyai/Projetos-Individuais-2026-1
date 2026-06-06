"""
Catálogo de Dados e Linhagem — garante idempotência (evita reprocessamento)
e registra a origem exata de cada dado extraído (data lineage).
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from src.models import DadosEmpresa

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/catalog.db")

# Garante que o diretório data/ existe antes de criar o banco
Path("data").mkdir(exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class Documento(Base):
    """Registro de um PDF coletado — chave de idempotência é o hash SHA-256."""

    __tablename__ = "documentos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    empresa = Column(String(100), nullable=False, index=True)
    url = Column(Text, nullable=False)
    hash_sha256 = Column(String(64), unique=True, nullable=False, index=True)
    nome_arquivo = Column(String(255), nullable=True)
    data_coleta = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    processado = Column(Boolean, default=False)

    dados = relationship("DadoExtraido", back_populates="documento")


class DadoExtraido(Base):
    """Dado estruturado extraído de um Documento — cada linha tem linhagem completa."""

    __tablename__ = "dados_extraidos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    documento_id = Column(Integer, ForeignKey("documentos.id"), nullable=False)

    # Dimensões temporais
    empresa = Column(String(100), nullable=False, index=True)
    ano = Column(Integer, nullable=False, index=True)
    trimestre = Column(Integer, nullable=False, index=True)
    tipo_relatorio = Column(String(50), nullable=False)

    # Métricas operacionais
    unidades_lancadas = Column(Integer, nullable=True)
    vgv_lancamentos_mil_reais = Column(Float, nullable=True)
    unidades_vendidas = Column(Integer, nullable=True)
    vendas_contratadas_mil_reais = Column(Float, nullable=True)
    vso_percentual = Column(Float, nullable=True)
    estoque_unidades = Column(Integer, nullable=True)
    estoque_vgv_mil_reais = Column(Float, nullable=True)
    landbank_unidades = Column(Integer, nullable=True)
    entregas_unidades = Column(Integer, nullable=True)

    # Métricas financeiras
    receita_liquida_mil_reais = Column(Float, nullable=True)
    lucro_bruto_mil_reais = Column(Float, nullable=True)
    margem_bruta_percentual = Column(Float, nullable=True)
    ebitda_ajustado_mil_reais = Column(Float, nullable=True)
    margem_ebitda_percentual = Column(Float, nullable=True)
    lucro_liquido_mil_reais = Column(Float, nullable=True)
    margem_liquida_percentual = Column(Float, nullable=True)
    divida_liquida_mil_reais = Column(Float, nullable=True)

    # Linhagem
    fonte_pdf_url = Column(Text, nullable=False)
    hash_documento = Column(String(64), nullable=False)
    data_processamento = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    observacoes_extracao = Column(Text, nullable=True)

    documento = relationship("Documento", back_populates="dados")


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def compute_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def documento_ja_processado(hash_sha256: str) -> bool:
    with SessionLocal() as session:
        doc = session.query(Documento).filter_by(hash_sha256=hash_sha256).first()
        return doc is not None and doc.processado


def registrar_documento(
    empresa: str,
    url: str,
    hash_sha256: str,
    nome_arquivo: Optional[str] = None,
) -> int:
    """Insere o documento no catálogo e retorna seu id."""
    with SessionLocal() as session:
        existing = session.query(Documento).filter_by(hash_sha256=hash_sha256).first()
        if existing:
            return existing.id
        doc = Documento(
            empresa=empresa,
            url=url,
            hash_sha256=hash_sha256,
            nome_arquivo=nome_arquivo,
        )
        session.add(doc)
        session.commit()
        session.refresh(doc)
        return doc.id


def salvar_dados_extraidos(
    documento_id: int,
    fonte_url: str,
    hash_documento: str,
    dados_empresa: DadosEmpresa,
    observacoes: Optional[str] = None,
) -> None:
    op = dados_empresa.metricas_operacionais
    fin = dados_empresa.metricas_financeiras

    with SessionLocal() as session:
        dado = DadoExtraido(
            documento_id=documento_id,
            empresa=dados_empresa.empresa,
            ano=dados_empresa.ano,
            trimestre=dados_empresa.trimestre,
            tipo_relatorio=dados_empresa.tipo_relatorio,
            unidades_lancadas=op.unidades_lancadas,
            vgv_lancamentos_mil_reais=op.vgv_lancamentos_mil_reais,
            unidades_vendidas=op.unidades_vendidas,
            vendas_contratadas_mil_reais=op.vendas_contratadas_mil_reais,
            vso_percentual=op.vso_percentual,
            estoque_unidades=op.estoque_unidades,
            estoque_vgv_mil_reais=op.estoque_vgv_mil_reais,
            landbank_unidades=op.landbank_unidades,
            entregas_unidades=op.entregas_unidades,
            receita_liquida_mil_reais=fin.receita_liquida_mil_reais,
            lucro_bruto_mil_reais=fin.lucro_bruto_mil_reais,
            margem_bruta_percentual=fin.margem_bruta_percentual,
            ebitda_ajustado_mil_reais=fin.ebitda_ajustado_mil_reais,
            margem_ebitda_percentual=fin.margem_ebitda_percentual,
            lucro_liquido_mil_reais=fin.lucro_liquido_mil_reais,
            margem_liquida_percentual=fin.margem_liquida_percentual,
            divida_liquida_mil_reais=fin.divida_liquida_mil_reais,
            fonte_pdf_url=fonte_url,
            hash_documento=hash_documento,
            observacoes_extracao=observacoes,
        )
        session.add(dado)
        session.commit()


def marcar_processado(documento_id: int) -> None:
    with SessionLocal() as session:
        doc = session.query(Documento).filter_by(id=documento_id).first()
        if doc:
            doc.processado = True
            session.commit()


def buscar_dados(
    empresa: Optional[str] = None,
    ano: Optional[int] = None,
    trimestre: Optional[int] = None,
) -> list[DadoExtraido]:
    with SessionLocal() as session:
        q = session.query(DadoExtraido)
        if empresa:
            q = q.filter(DadoExtraido.empresa.ilike(f"%{empresa}%"))
        if ano:
            q = q.filter(DadoExtraido.ano == ano)
        if trimestre:
            q = q.filter(DadoExtraido.trimestre == trimestre)
        rows = q.order_by(DadoExtraido.empresa, DadoExtraido.ano, DadoExtraido.trimestre).all()
        session.expunge_all()
        return rows


def listar_documentos() -> list[Documento]:
    with SessionLocal() as session:
        docs = session.query(Documento).order_by(Documento.data_coleta.desc()).all()
        session.expunge_all()
        return docs
