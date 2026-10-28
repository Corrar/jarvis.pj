import sys

argumentos = sys.argv[1:]
escondido = "--segundo-plano" in argumentos and sys.platform == "win32"
if escondido:
    if not any(a.startswith("--console") for a in argumentos):
        # Chamado à mão: confere as opções aqui, no terminal de quem digitou, antes de subir o outro processo.
        from jarvis.cli import analisador_de_argumentos

        analisador_de_argumentos().parse_args(argumentos)
    # Antes de carregar o resto: o terminal some já, não depois de alguns segundos.
    from jarvis.segundo_plano import assumir_console

    assumir_console(argumentos)

# Criar ou apagar o atalho não precisa do Jarvis inteiro de pé: se as bibliotecas não estiverem
# instaladas NESTE Python, ainda assim tem de dar para instalar o atalho (ele vai procurar o Python
# certo). Por isso a opção é tratada antes do import que puxa o SDK.
if "--criar-atalho" in argumentos or "--remover-atalho" in argumentos:
    from jarvis.atalhos import ajustar_atalhos

    raise SystemExit(ajustar_atalhos("--criar-atalho" in argumentos))

try:
    from jarvis.cli import main

    codigo = main()
except Exception as erro:  # noqa: BLE001 - com o terminal escondido, um erro na subida precisa aparecer em algum lugar
    if not escondido:
        raise
    from jarvis.segundo_plano import caixa

    caixa(
        f"O Jarvis não subiu.\n\n{type(erro).__name__}: {erro}\n\n"
        "Se faltou uma biblioteca, abra um terminal na pasta do Jarvis e rode: pip install -r requirements.txt"
    )
    codigo = 1
except SystemExit as saida:
    # Opção recusada (o texto da recusa foi para o terminal, que está escondido).
    if not escondido or saida.code in (0, None):
        raise
    from jarvis.segundo_plano import caixa

    caixa(f"O Jarvis não subiu: as opções com que foi chamado não foram aceitas.\n\n{' '.join(argumentos)}")
    codigo = 2

raise SystemExit(codigo)
