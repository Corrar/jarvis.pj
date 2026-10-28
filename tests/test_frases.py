import pytest

from jarvis.voz.frases import Separador, limpar_para_fala, tem_o_que_falar


def separar(texto: str, tamanho: int = 3) -> list[str]:
    """Alimenta o separador em pedaços pequenos, como o modelo entrega."""
    sep, frases = Separador(), []
    for i in range(0, len(texto), tamanho):
        frases += sep.alimentar(texto[i : i + tamanho])
    return frases + sep.descarregar()


def test_frase_sai_assim_que_fecha():
    sep = Separador()
    assert sep.alimentar("São nove horas") == []
    assert sep.alimentar(". ") == ["São nove horas."]
    assert sep.alimentar("E está sol") == []
    assert sep.descarregar() == ["E está sol"]


def test_resultado_nao_depende_do_tamanho_do_pedaco():
    texto = "O dólar está em R$ 4,98 hoje. Caiu 0,55%! Quer mais detalhes? Posso buscar."
    esperado = ["O dólar está em R$ 4,98 hoje.", "Caiu 0,55%!", "Quer mais detalhes?", "Posso buscar."]
    for tamanho in (1, 2, 5, 13, 200):
        assert separar(texto, tamanho) == esperado


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("O valor é 1.234,56 no total. Fim.", ["O valor é 1.234,56 no total.", "Fim."]),
        ("Fale com o Dr. Silva amanhã. Combinado.", ["Fale com o Dr. Silva amanhã.", "Combinado."]),
        ("Vi relatórios, pedidos etc. e mais nada.", ["Vi relatórios, pedidos etc. e mais nada."]),
        ("Ele disse \"pronto.\" Depois saiu.", ['Ele disse "pronto."', "Depois saiu."]),
        ("Espere… Já volto.", ["Espere…", "Já volto."]),
        ("Linha um\nLinha dois", ["Linha um", "Linha dois"]),
        ("1. Primeiro item.\n2. Segundo item.", ["1. Primeiro item.", "2. Segundo item."]),
        ("Foi em 2026. O mercado reagiu.", ["Foi em 2026.", "O mercado reagiu."]),
    ],
)
def test_casos_de_pontuacao(texto, esperado):
    assert separar(texto) == esperado


def test_frase_longa_sem_ponto_e_cortada_na_virgula():
    texto = ", ".join(f"arquivo número {n} da pasta de relatórios" for n in range(12))
    frases = separar(texto, 7)
    assert len(frases) > 1
    assert all(len(f) <= 240 for f in frases)
    assert " ".join(frases).replace(", ", " ").split() == texto.replace(", ", " ").split()


def test_descarregar_zera_o_estado():
    sep = Separador()
    sep.alimentar("resto sem ponto")
    assert sep.descarregar() == ["resto sem ponto"]
    assert sep.descarregar() == []


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("- Relatórios da **Royale**", "Relatórios da Royale"),
        ("1. Primeiro item.", "Primeiro item."),
        ("## Título", "Título"),
        ("Arquivo `Relatorio_Royale.pdf`", "Arquivo Relatorio Royale.pdf"),
        ("Veja [InfoMoney](https://infomoney.com.br/x) e https://exemplo.com/y", "Veja InfoMoney e link"),
        ("O dólar está em R$ 4,98 hoje.", "O dólar está em 4,98 reais hoje."),
        ("Custou US$ 0,03 e R$ 1.234,56.", "Custou 0,03 dólares e 1234,56 reais."),
        ("texto\n\ncom   espaços", "texto com espaços"),
    ],
)
def test_limpar_para_fala(texto, esperado):
    assert limpar_para_fala(texto) == esperado


def test_tem_o_que_falar():
    assert tem_o_que_falar("ok")
    assert not tem_o_que_falar(" … - ")
