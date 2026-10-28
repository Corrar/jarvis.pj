# Jarvis: inventário diário Fluxo → Protheus (SB7) pela tela

**Versão 1 · 23/10/2026** · Para: quem desenvolve o Jarvis, e o Bruno
**Fonte:** sessão "Integração Fluxo → TOTVS Protheus", com base no código do Fluxo5.0-Backend (`main`) e
nas leituras do Protheus de produção feitas em 22 e 23/10/2026.

---

## 1. Objetivo

Todo dia útil às **17:00**, o Jarvis:

1. lê o estoque do Fluxo Royale;
2. transforma o estoque na lista de inventário do Protheus (SB7);
3. **pede a aprovação do Bruno**;
4. abre o Protheus na máquina do Bruno e **digita** a lista na rotina **MATA270** (digitação de
   inventário), como uma pessoa faria;
5. confere o que digitou e entrega um relatório.

**Por que pela tela:** em produção, o Protheus não publica a MATA270 nem nenhuma outra rota de gravação de
inventário no REST. Isso foi medido: `fwmodel/MATA270` volta 404, e a busca no catálogo de 2.910 rotas
não achou `mata270`, `sb7` nem `mata340`. A tela é a ponte até existir o endpoint ADVPL
`POST /fluxo/v1/inventario`. Quando ele existir, esta automação é aposentada.

## 2. Fora do escopo (o Jarvis NÃO faz)

- **Acerto de inventário (MATA340).** O acerto muda o saldo e o custo do estoque. Fica com o fiscal ou o
  almoxarifado, na mão. Se um dia entrar, vira outra especificação, com outra aprovação.
- Excluir ou alterar lançamento de inventário que já existia antes da execução do dia.
- Abrir qualquer outra rotina do Protheus além da MATA270 e da consulta que confere o que foi digitado.
- Gravar qualquer coisa no Fluxo.
- Rodar sem a aprovação do dia (ver §6).

## 3. Pré-requisitos

| # | Item | Responsável |
|---|---|---|
| P-1 | Módulo de **controle de tela** (mouse, teclado e captura de tela) no Jarvis. Não existe na 0.19. | Desenvolvedor do Jarvis |
| P-2 | **Usuário próprio do Jarvis no Fluxo**, só com leitura de estoque (sem nenhuma chave de escrita). Login e senha no `.env` do Jarvis, nunca no chat. | Bruno, no Fluxo |
| P-3 | **Usuário do Protheus** que o Jarvis vai usar na tela. Recomendado: um usuário próprio (ex.: `JARVIS`) com acesso **só à MATA270 e à consulta**, para a auditoria do Protheus mostrar quem lançou. Se for o usuário do Bruno, registrar essa decisão. | Bruno com a consultoria |
| P-4 | Computador **ligado, com a sessão do Windows aberta e a tela desbloqueada** às 17:00. Sem bloqueio automático de tela entre 16:55 e 17:30. | Bruno |
| P-5 | De-para **SKU do Fluxo → B1_COD do Protheus** (pergunta P3 da especificação do parceiro). Enquanto não houver, a lista sai com o SKU e a calibração (§8) confirma se ele é aceito. | Bruno com a consultoria |
| P-6 | Decisão do fiscal: o saldo do Fluxo pode ser lançado como **contagem** na SB7 todo dia? A SB7 é, por definição, contagem física. | Fiscal / arquiteto |

## 4. Ajustes novos no `.env` do Jarvis

| Ajuste | Valores | Padrão |
|---|---|---|
| `JARVIS_INV_MODO` | `desligado` · `homologacao` · `producao` | `desligado` |
| `JARVIS_INV_HORA` | `HH:MM` | `17:00` |
| `JARVIS_INV_DIAS` | dias da semana (`seg,ter,qua,qui,sex`) | `seg,ter,qua,qui,sex` |
| `JARVIS_INV_MAX_ITENS` | teto de itens por execução | `300` |
| `JARVIS_FLUXO_URL` | endereço da API do Fluxo | — |
| `JARVIS_FLUXO_USER` / `JARVIS_FLUXO_PASS` | usuário de leitura do Fluxo (P-2) | — |

**Ligar em `producao` só depois do aceite em homologação (§9).** O conferidor do `.env` (0.18) precisa
conhecer esses nomes.

## 5. Etapa A: ler o Fluxo e montar a lista (sem tela)

**Leitura**, só HTTP GET, mais o login:

1. `POST {JARVIS_FLUXO_URL}/auth/login` com `{ "email", "password" }` do `.env`. O token vale 1 dia: faça
   login a cada execução e não guarde o token.
2. `GET {JARVIS_FLUXO_URL}/stock` com `Authorization: Bearer <token>`.
   - Devolve **uma linha por produto do armazém ALMOX**, só produtos ativos. É o único armazém da rota.
   - Campos usados: `quantity_on_hand` (saldo físico), `products.sku`, `products.name`, `products.unit`.
   - ALMOX corresponde ao armazém **02** do Protheus (`warehouses.protheus_code`). Isso bate com a leitura
     do Protheus de produção: armazém 02 = ALMOXARIFADO.

**Montagem**: uma linha por produto:

| Campo SB7 (provável; confirmar na calibração) | Valor |
|---|---|
| B7_FILIAL | `0101` (filial lida em produção) |
| B7_COD | `products.sku`, ou o B1_COD do de-para (P-5) |
| B7_LOCAL | `02` |
| B7_DOC | `FLX` + data `AAMMDD` (ex.: `FLX261023`). Confirmar o tamanho do campo. |
| B7_QUANT | `quantity_on_hand` |
| B7_DATA | data do dia |

**Barreiras da etapa A.** Qualquer uma delas interrompe a execução **antes** da tela e avisa o Bruno:

- falha de login ou de leitura, ou resposta vazia;
- mais itens que `JARVIS_INV_MAX_ITENS`;
- SKU vazio ou repetido;
- quantidade negativa ou não numérica;
- data do dia que não seja dia configurado, ou execução de hoje já concluída (ver §7, "uma vez por dia").

**Saída:** `Documentos\Inventario-Fluxo\AAAA-MM-DD\lista.csv` (separador `;`, decimal com vírgula), mais um
resumo com o total de itens, os itens com saldo zero e a soma das quantidades.

## 6. Etapa B: aprovação do Bruno

Às 17:00, depois da etapa A, o Jarvis avisa pela voz e pelo painel:

> "Inventário de hoje pronto: **N itens**, armazém 02, documento FLX261023. Zeros: X. Posso digitar no
> Protheus **[homologação/produção]**?"

- Só segue com um **"sim" falado ou clicado no painel pelo Bruno**, dentro de **60 minutos**.
- Sem resposta, **não roda** e registra "não aprovado".
- "Não", ou qualquer outra resposta: não roda.
- A aprovação vale **para a lista daquele dia**. Se a lista mudar, a aprovação cai e o Jarvis pede de novo.

A aprovação vem **antes** da digitação porque, na MATA270, cada item costuma ser gravado ao confirmar a
própria tela. Não existe um "confirmar tudo" no fim. A calibração (§8) confirma o comportamento. Se
houver confirmação única no fim, o Jarvis **para antes dela** e pede um segundo "sim".

## 7. Etapa C: digitar na MATA270 (pela tela)

### Regras de segurança da tela, que valem o tempo todo

1. **Ambiente certo.** Antes de digitar, o Jarvis lê na tela (título da janela ou barra de status) o
   **nome do ambiente e a empresa/filial** e compara com `JARVIS_INV_MODO`. Homologação =
   `royale191155`; produção = `royale191154`; filial `0101`. Se não bater, para.
2. **Rotina certa.** Só digita com a tela da MATA270 aberta, reconhecida pelo título. Qualquer outra tela
   ou pop-up que não esteja no roteiro: para.
3. **Leitura de volta, campo a campo.** Depois de digitar cada campo, o Jarvis lê na tela o valor que
   ficou e compara com o da lista. Se divergir, **não confirma** aquele item: cancela a tela do item e
   para.
4. **Mensagens do Protheus.** Qualquer janela de aviso ou erro (ex.: "produto não cadastrado", "armazém
   inválido", "período fechado", "registro já existe"): o Jarvis tira uma captura de tela, lê o texto,
   **cancela o item** e **para a execução inteira**. Não tenta contornar e não clica em "Sim" em pergunta
   que não esteja no roteiro.
5. **Botão de pânico.** Mover o mouse para o canto superior esquerdo ou apertar `Esc` três vezes para a
   automação na hora. Se o Bruno mexer no mouse ou no teclado durante a execução, ela pausa e pergunta.
6. **Ritmo humano.** Esperar a tela responder (até 15 s por passo). Lentidão acima disso: para.
7. **Uma vez por dia.** Se já existe execução concluída para a data, não roda de novo. Uma nova tentativa
   no mesmo dia só com pedido explícito do Bruno, e continuando **do item seguinte ao último confirmado**,
   nunca do começo.
8. **Sem atalho por fora da tela.** Nada de colar SQL, abrir o Configurador, mexer em parâmetro ou rodar
   fórmula. Só os campos da MATA270.

### Roteiro (a calibração preenche os detalhes exatos da tela)

1. Abrir o SmartClient (ou o navegador, se o acesso for web) e entrar com o usuário de P-3.
2. Conferir o ambiente e a filial (regra 1).
3. Abrir a MATA270 pelo caminho de menu registrado na calibração.
4. Para cada item da lista, na ordem:
   1. clicar em **Incluir**;
   2. preencher **Documento, Produto, Armazém, Data e Quantidade** (e Lote/Endereço, se a calibração disser
      que são obrigatórios);
   3. ler de volta cada campo (regra 3);
   4. **Confirmar**;
   5. registrar no log: item, hora, "confirmado".
5. Ao terminar, fechar a MATA270 e sair do Protheus.

## 8. Calibração (uma vez, em homologação, com o Bruno do lado)

Antes da primeira execução automática, o Bruno faz o caminho na mão em homologação e o Jarvis **observa
e registra**:

- caminho do menu até a MATA270;
- nome e ordem dos campos da tela, quais são obrigatórios e os tamanhos;
- se cada item grava ao confirmar ou se há confirmação única no fim (define a §6);
- as mensagens que aparecem num lançamento normal (para não confundir com erro);
- se o B7_COD aceita o SKU do Fluxo (P-5);
- se o mesmo produto/armazém/documento no mesmo dia dá erro de duplicidade.

O resultado vira um arquivo de roteiro versionado (`roteiros/mata270.yaml`) que o Bruno revisa. **Mudou a
tela do Protheus (atualização de release), recalibra.**

## 9. Teste de aceite (homologação, antes de ligar produção)

| # | Cenário | Esperado |
|---|---|---|
| T1 | Execução normal com 5 itens | 5 lançamentos na SB7 de homologação, relatório "5/5" |
| T2 | Sem resposta na aprovação | Nada digitado, registro "não aprovado" |
| T3 | Produto inexistente no meio da lista | Para nesse item, os anteriores ficam, relatório com a mensagem do Protheus |
| T4 | Ambiente errado aberto (produção com modo `homologacao`) | Para antes de digitar |
| T5 | Bruno mexe no mouse no meio | Pausa e pergunta |
| T6 | Segunda execução no mesmo dia | Recusa |
| T7 | Lista acima do teto | Para na etapa A |
| T8 | Tela bloqueada às 17:00 | Não roda, avisa quando a tela for desbloqueada |
| T9 | Uma semana seguida de execuções reais em homologação | Conferência (§10) bate em todos os dias |

**Só depois de T1 a T9 aprovados o Bruno muda `JARVIS_INV_MODO=producao`.**

## 10. Conferência e relatório

Ao fim de cada execução:

- **Conferência:** abrir a consulta da SB7 (browse da MATA270, filtrado pelo documento do dia) e comparar
  com a lista: quantidade de itens, produto a produto, quantidade a quantidade. Para a conferência por
  REST, a ferramenta precisaria liberar `/api/stock/analysis/v1/countresults`, que está no catálogo de
  produção. Fica para uma versão futura.
- **Relatório** para o Bruno (voz + painel + arquivo `relatorio.md` na pasta do dia):
  - itens da lista / digitados / confirmados / conferidos;
  - divergências e erros, com captura de tela;
  - tempo total;
  - **lembrete:** "o acerto (MATA340) não foi feito; é com o fiscal".
- **Log** de cada passo na pasta do dia, **sem senha**. As capturas de tela ficam na pasta do dia por
  30 dias.

## 11. Perguntas em aberto

1. Quantos itens, mais ou menos, por dia? Define o teto e o tempo. Acima de ~200 itens, a digitação pela
   tela fica lenta, e o endpoint ADVPL passa a valer muito mais.
2. O Protheus abre pelo **SmartClient instalado** ou pelo **navegador** (WebApp)? Muda o jeito de ler a
   tela.
3. Usuário próprio do Jarvis no Protheus (P-3) ou o do Bruno?
4. O fiscal aprova lançar o saldo do Fluxo como contagem diária (P-6)?
5. Documento do dia: `FLX` + data serve, ou o almoxarifado já usa outra numeração?
6. Só o armazém 02 (ALMOX), ou outros setores também? Hoje a rota do Fluxo devolve só o ALMOX, e só ALMOX
   (02) e USINAGEM (12) têm código do Protheus.
