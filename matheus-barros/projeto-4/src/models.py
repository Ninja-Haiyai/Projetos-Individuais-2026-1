"""
Contrato Semântico — define os tipos e regras que forçam o LLM a retornar
dados bem-tipados e a tratar campos ausentes como NULL em vez de alucinar.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class MetricasOperacionais(BaseModel):
    """Métricas extraídas da prévia operacional / relatório trimestral."""

    unidades_lancadas: Optional[int] = Field(
        None,
        description=(
            "Total de unidades habitacionais lançadas no trimestre. "
            "Retorne NULL se o valor não constar no documento."
        ),
    )
    vgv_lancamentos_mil_reais: Optional[float] = Field(
        None,
        description=(
            "VGV (Valor Geral de Vendas) dos lançamentos em R$ mil. "
            "Converta para R$ mil se estiver em outra unidade. "
            "Retorne NULL se ausente."
        ),
    )
    unidades_vendidas: Optional[int] = Field(
        None,
        description=(
            "Total de unidades vendidas/contratadas no trimestre. "
            "Retorne NULL se ausente."
        ),
    )
    vendas_contratadas_mil_reais: Optional[float] = Field(
        None,
        description=(
            "Valor total de vendas contratadas (VSO) em R$ mil. "
            "Use o valor ABSOLUTO — ignore variações percentuais (%a.a., YoY, etc.). "
            "Retorne NULL se ausente."
        ),
    )
    vso_percentual: Optional[float] = Field(
        None,
        description=(
            "Velocidade de Vendas sobre Oferta (VSO) em %. "
            "Ex: 18.5 para 18,5%. Retorne NULL se ausente."
        ),
    )
    estoque_unidades: Optional[int] = Field(
        None,
        description="Unidades em estoque ao final do trimestre. Retorne NULL se ausente.",
    )
    estoque_vgv_mil_reais: Optional[float] = Field(
        None,
        description="VGV do estoque em R$ mil. Retorne NULL se ausente.",
    )
    landbank_unidades: Optional[int] = Field(
        None,
        description="Total de unidades no banco de terrenos (landbank). Retorne NULL se ausente.",
    )
    entregas_unidades: Optional[int] = Field(
        None,
        description="Unidades entregues (concluídas/habite-se) no trimestre. Retorne NULL se ausente.",
    )


class MetricasFinanceiras(BaseModel):
    """Métricas financeiras do resultado trimestral (DRE / earnings release)."""

    receita_liquida_mil_reais: Optional[float] = Field(
        None,
        description=(
            "Receita Líquida em R$ mil. "
            "Use o valor ABSOLUTO do período — ignore variação percentual. "
            "Retorne NULL se ausente."
        ),
    )
    lucro_bruto_mil_reais: Optional[float] = Field(
        None,
        description="Lucro Bruto em R$ mil. Retorne NULL se ausente.",
    )
    margem_bruta_percentual: Optional[float] = Field(
        None,
        description="Margem Bruta em %. Ex: 34.2 para 34,2%. Retorne NULL se ausente.",
    )
    ebitda_ajustado_mil_reais: Optional[float] = Field(
        None,
        description="EBITDA Ajustado em R$ mil. Retorne NULL se ausente.",
    )
    margem_ebitda_percentual: Optional[float] = Field(
        None,
        description="Margem EBITDA em %. Retorne NULL se ausente.",
    )
    lucro_liquido_mil_reais: Optional[float] = Field(
        None,
        description="Lucro Líquido em R$ mil. Retorne NULL se ausente.",
    )
    margem_liquida_percentual: Optional[float] = Field(
        None,
        description="Margem Líquida em %. Retorne NULL se ausente.",
    )
    divida_liquida_mil_reais: Optional[float] = Field(
        None,
        description=(
            "Dívida Líquida em R$ mil (positivo = endividado, negativo = caixa líquido). "
            "Retorne NULL se ausente."
        ),
    )


class DadosEmpresa(BaseModel):
    """Dados consolidados de uma empresa para um determinado trimestre."""

    empresa: str = Field(
        ...,
        description=(
            "Nome comercial da construtora/incorporadora exatamente como aparece no documento. "
            "Ex: 'MRV', 'Direcional', 'Tenda', 'Cury', 'Plano&Plano', 'Pacaembu'."
        ),
    )
    ano: int = Field(
        ...,
        description="Ano de referência do período. Ex: 2025.",
    )
    trimestre: int = Field(
        ...,
        ge=1,
        le=4,
        description="Trimestre de referência (1, 2, 3 ou 4).",
    )
    tipo_relatorio: str = Field(
        ...,
        description=(
            "Tipo do documento de origem. Valores aceitos: "
            "'previa_operacional', 'resultado_trimestral', 'boletim_conjuntura', 'outro'."
        ),
    )
    metricas_operacionais: MetricasOperacionais = Field(
        default_factory=MetricasOperacionais,
        description="Métricas operacionais extraídas do documento.",
    )
    metricas_financeiras: MetricasFinanceiras = Field(
        default_factory=MetricasFinanceiras,
        description="Métricas financeiras extraídas do documento.",
    )


class RespostaExtracao(BaseModel):
    """Envelope de resposta do LLM: lista de empresas extraídas do documento."""

    empresas: list[DadosEmpresa] = Field(
        ...,
        description=(
            "Lista com os dados de cada empresa identificada no documento. "
            "Se o documento cobrir apenas uma empresa, retorne uma lista com um único item. "
            "Nunca invente dados; use NULL para campos ausentes."
        ),
    )
    observacoes: Optional[str] = Field(
        None,
        description=(
            "Observações relevantes sobre a extração: ambiguidades, "
            "unidades não padronizadas, dados parciais, etc."
        ),
    )
