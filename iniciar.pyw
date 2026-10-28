"""Sobe o Jarvis escondido, com o ícone perto do relógio.

Dois cliques neste arquivo ligam o Jarvis em segundo plano. É também o que o Windows chama
ao entrar, depois de `python -m jarvis --iniciar-com-windows`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from jarvis.segundo_plano import lancar

    lancar()
except Exception as erro:  # noqa: BLE001 - sem terminal, o erro precisa aparecer em algum lugar
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, f"Não consegui iniciar o Jarvis.\n\n{type(erro).__name__}: {erro}", "Jarvis", 0x10)
