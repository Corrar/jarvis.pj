"""Mensagens para os erros que a API da Anthropic devolve, usadas na conversa e nos trabalhos."""

from __future__ import annotations

ERROS_DA_API = {
    "authentication_failed": "A Anthropic recusou a chave. Confira ANTHROPIC_API_KEY no arquivo .env.",
    "billing_error": "A conta da Anthropic está sem crédito ou com problema de cobrança.",
    "rate_limit": "Limite de uso da API atingido. Espere um pouco e tente de novo.",
    "invalid_request": "A API recusou o pedido como inválido.",
    "server_error": "A API da Anthropic falhou do lado dela. Tente de novo.",
}


def erro_da_api(codigo: str) -> str:
    return ERROS_DA_API.get(codigo, f"Falha na API: {codigo}")
