# Jarvis 0.20.0 — inventário diário Fluxo → Protheus (etapa A)

23/10/2026 · sobre a especificação **ESPEC-JARVIS-RPA-INVENTARIO-SB7 v1**

---

## O que entrou

A **etapa A inteira** da especificação: ler o Fluxo, montar a lista do inventário e guardar o dia.
A **etapa C** (digitar na MATA270) **não entrou**, de propósito — o porquê está na seção "O que
falta".

Todo dia útil às 17:00, sozinho:

1. faz login no Fluxo e lê o estoque do ALMOX;
2. monta uma linha de SB7 por produto;
3. grava `Documentos\Inventario-Fluxo\AAAA-MM-DD\` com `lista.csv`, `execucao.json` e
   `relatorio.md`;
4. avisa pela voz e pelo terminal o que achou;
5. para, dizendo o que falta para digitar.

Se a máquina estava desligada às 17:00, ele roda assim que o Jarvis subir. O dia não se perde, e
também não roda duas vezes.

### Comandos

| Comando | O que faz |
|---|---|
| `/inventario` | situação de hoje: estado, itens, documento, pasta |
| `/inventario montar` | roda a etapa A na hora, sem esperar as 17:00 |
| `/inventario aprovar` | libera a digitação (hoje, recusa dizendo o que falta) |

### Ajustes no `.env`

```
JARVIS_INV_MODO=homologacao      # desligado · homologacao · producao
JARVIS_INV_HORA=17:00
JARVIS_INV_DIAS=seg,ter,qua,qui,sex
JARVIS_INV_MAX_ITENS=300
JARVIS_FLUXO_URL=https://...
JARVIS_FLUXO_USER=usuario-de-leitura-do-fluxo
JARVIS_FLUXO_PASS=
```

Todos entraram no conferidor do `.env` (0.18): nome trocado vira aviso na partida, com o nome certo
ao lado. `JARVIS_INV_MODO` ligado sem `JARVIS_FLUXO_URL` e `JARVIS_FLUXO_USER` **impede o Jarvis de
subir**, em vez de rodar pela metade.

---

## O que protege, pelo código

| Trava | O que faz |
|---|---|
| Duas rotas, fixas | `POST /auth/login` e `GET /stock`. Nada mais sai daqui. |
| **O modelo não participa** | O inventário não é ferramenta dele: roda pelo relógio e pelo seu comando digitado. Modelo nenhum dispara lançamento em ERP, nem se alguém pedir. |
| Sem seguir desvio | No login e na leitura. Seguir um 3xx mandaria a senha do Fluxo para o endereço que o servidor escolhesse. |
| Token não guardado | Vale um dia; um login por execução custa menos que um segredo parado em disco. |
| Senha fora de tudo | Nunca aparece em mensagem, relatório ou log — nem quando o Fluxo devolve a senha dentro do texto do erro. |
| Uma vez por dia | Gravado em disco: reiniciar o Jarvis não devolve o direito de lançar. |
| Acerto não é feito | A MATA340 fica fora, e o relatório lembra disso todo dia: é com o fiscal. |

### Barreiras da etapa A

Qualquer uma interrompe **antes** de qualquer tela, com motivo em português:

- falha de login, de leitura, ou resposta que não é JSON;
- estoque vazio (não digito inventário vazio);
- SKU vazio, repetido (`ABC` e `abc` contam como o mesmo) ou **com caractere que eu não digitaria
  numa tela de produção**;
- quantidade negativa, não numérica, nula ou infinita;
- lista acima de `JARVIS_INV_MAX_ITENS`;
- **teto absoluto de 5000 linhas**, acima do configurável: resposta maior que isso é defeito do
  Fluxo, não inventário.

As duas últimas em negrito são acréscimos meus à especificação.

---

## Três correções à especificação

### 1. O P-1 está errado: controle de tela já existe

O `janelas.py` do Jarvis fotografa uma janela, clica e digita, restrito a uma lista de programas que
você controla no `.env`. O SmartClient entraria nessa lista hoje.

Isso **não** resolve a etapa C, por um motivo mais importante que o que a especificação levantou:
hoje quem clica é o **modelo**, olhando uma foto, com teto de 40 ações por turno. Para 300 linhas
determinísticas em produção, isso é o oposto do que se quer — modelo olhando foto erra, e erra
diferente a cada vez.

A etapa C tem de ser um **roteiro determinístico**: ordem de campos, teclas, leitura de volta campo
a campo, com o modelo entrando só para ler o texto de uma caixa de erro. Por isso o
`roteiros/mata270.yaml` da sua §8 não é um detalhe de implementação: é a peça que falta de verdade.

### 2. O "sim" falado não aprova

A §6 aceita "sim falado ou clicado". **Falar não confirma gravação em produção.** A televisão, um
colega na sala ou o próprio alto-falante do Jarvis produzem um "sim"; um `s` digitado ou um clique
não acontecem sozinhos.

Ficou assim: ele **pergunta** pela voz, a resposta vem pela mão (`/inventario aprovar`). É a mesma
regra que já vale para comandos de terminal. Se você quiser diferente, é decisão sua — mas eu não
faria.

### 3. Sem roteiro, ele não pede aprovação

Pedir "sim" para algo que não vai acontecer é ritual vazio, e ritual vazio ensina a aprovar sem ler.
Enquanto não houver roteiro calibrado, ele monta a lista, avisa que está pronta e diz o que falta.
Quando o roteiro existir, a pergunta do §6 passa a ser feita, com o prazo de 60 minutos, e a
aprovação cai se a lista mudar.

---

## O que falta para a etapa C

Em ordem de importância:

1. **O roteiro calibrado da tela** (§8): caminho do menu, nome e ordem dos campos, quais são
   obrigatórios, tamanhos, se cada item grava ao confirmar ou se há confirmação única no fim, e
   quais mensagens são normais num lançamento bom.
2. **SmartClient instalado ou navegador?** (sua pergunta 2). Muda como leio a tela, e é a diferença
   entre dois caminhos de implementação.
3. **O usuário do Protheus** que o Jarvis vai usar (P-3).
4. **O de-para SKU → B1_COD** (P-5), ou a confirmação de que o B7_COD aceita o SKU do Fluxo.

Os itens 3 e 4 são com você e a consultoria; o 1 e o 2 saem juntos, na calibração.

### Ainda em aberto, com o fiscal

A pergunta P-6 continua a mais importante das suas: **o saldo do Fluxo pode ser lançado como
contagem na SB7 todo dia?** A SB7 é, por definição, contagem física. Isso não é decisão técnica, e
nenhuma trava de código resolve.

---

## Como ligar

1. Atualize para a 0.20.0.
2. Ponha no `.env` o `JARVIS_FLUXO_URL`, `JARVIS_FLUXO_USER` e `JARVIS_FLUXO_PASS` do usuário de
   leitura (P-2 — usuário próprio do Jarvis, só leitura de estoque), e
   `JARVIS_INV_MODO=homologacao`.
3. Reinicie e digite `/inventario montar`.
4. Abra `Documentos\Inventario-Fluxo\<hoje>\lista.csv` e confira contra o Fluxo.

Esse CSV já serve: dá para importar ou digitar à mão por ele enquanto a etapa C não existe.

---

## Conferência

1874 testes passando, 32 só do inventário, no Python daqui e no do Windows, mais o teste de ponta a
ponta com o programa real do Claude Code.

Os 32 cobrem, entre outros: as duas rotas e só elas; o token não ficar guardado; a senha não vazar
nem quando volta dentro da mensagem de erro; o desvio recusado no login e na leitura; cada barreira
da etapa A; o CSV com decimal em vírgula; a uma-vez-por-dia sobrevivendo a reinício; a aprovação
vencendo em 60 minutos e caindo quando a lista muda; a recusa da etapa C listando o que falta; e o
relógio disparando uma vez só, respeitando o dia da semana e recuperando o horário perdido.

**Um defeito meu que o lint pegou e os testes não:** eu construía o inventário numa função e usava
em outra, o que estouraria na primeira vez que você digitasse `/inventario`. Corrigido, com um teste
que trava essa ligação para o futuro.
