# Jarvis — o que foi otimizado, da 0.15.1 à 0.19.0

22 de outubro de 2026 · 6 versões em um dia

---

## Resumo

| Versão | O que entrou |
|---|---|
| **0.16.0** | Ler o Protheus de produção, só leitura. 21 problemas achados em duas revisões, todos corrigidos antes de liberar. |
| **0.17.0** | Atalho com ícone na Área de Trabalho e no menu Iniciar. Mais um defeito do painel achado no caminho. |
| **0.17.1** | O instalador do atalho passou a achar sozinho o Python certo. |
| **0.18.0** | Conferidor do `.env`, comando `/ajustes`, e a regra do turno limpo virou ajuste seu. |
| **0.18.1** | A voz da ElevenLabs: ID inválido não derruba mais o recurso inteiro. |
| **0.19.0** | Protheus: paginação, filtro por igualdade e busca no catálogo em fluxo. |

**1842 testes passando**, no Python daqui e no do Windows, mais um teste de ponta a ponta com o
programa real do Claude Code conversando com um Protheus de mentira.

---

## 1. Ler o Protheus (0.16.0 e 0.19.0)

### O que ele faz

Consulta o ERP em vez de supor o dicionário, sempre com GET:

- **busca no catálogo do REST** — que endereços o ambiente publica;
- **modelo de uma rotina** (`fwmodel`) — submodelos e nomes de campo, com paginação e filtro;
- **healthcheck** — se o serviço está de pé.

### As travas, que estão no código e não em recomendação ao modelo

| Trava | O que faz |
|---|---|
| Só GET | Não existe no arquivo caminho para POST, PUT, PATCH ou DELETE. Escrever no Protheus ele não faz, nem se você pedir. |
| Lista fechada de rotinas | O modelo não passa URL: diz qual rotina quer, de uma lista que você controla no `.env`. Rotina de fora é recusada em duas camadas e nem chega à rede. |
| Paginação conferida | `pageSize` de 1 a 50, `page` de 1 a 1000, lidos como número inteiro antes de montar a URL. |
| Filtro fechado | Só igualdade, em campo de lista fechada por rotina (`A2_CGC` e `A2_COD` no MATA020), e valor só com letra e dígito, até 40. |
| Não segue desvio (3xx) | Quem responde não escolhe para onde vai a sua credencial. |
| Teto de leituras | 15 por fala sua, 5 em turno automático, 120 no dia — e a conta do dia sobrevive a reiniciar o Jarvis. |
| Para na primeira dor | Erro 5xx, resposta lenta ou falha de rede travam as leituras até você falar de novo. Ele não insiste em produção. |
| Dado pessoal mascarado | CPF, CNPJ, e-mail, telefone, banco, agência, conta bancária e endereço viram `***`. O **nome do campo fica** — é dele que a integração precisa. |
| Informação, não ordem | Depois de ler o ERP, gravar na memória e as ações diretas ficam bloqueadas até a sua próxima fala. |

### Por que o filtro é seguro

As duas travas se sustentam juntas: **o campo não é escolhido pelo modelo** (lista fechada por
rotina) e **o valor não tem como sair das aspas** (só letra e dígito, sem espaço, aspa ou
operador). Um CNPJ vai só com os dígitos; com ponto e barra é recusado. Testado com `' or '1'='1`,
`1 or 1=1`, `x';DROP--` e afins — nenhum chega à rede.

### A busca no catálogo

Antes o corpo vinha cortado em 4 KB, o que escondia justamente a rota procurada. Agora o catálogo é
lido em fluxo, até 20 MB, sem ficar na memória nem ir para a conversa, e o que volta são as rotas
que contêm os termos mais quantas existem no total. A régua de lentidão dessa leitura é outra
(40 s em vez de 10), senão baixar um catálogo grande travaria o turno sozinho.

### O que as revisões acharam

Duas revisões independentes antes de liberar, por ser acesso a produção. 21 problemas. Os cinco
que mais importavam:

1. **A credencial vazava num desvio.** Se um proxy ou portal de rede na frente do Protheus
   respondesse "vá para outro endereço", a biblioteca seguia levando usuário e senha em texto claro
   para esse outro host — e o relatório ainda diria que leu do endereço certo. Reproduzi o
   vazamento com dois servidores locais antes de corrigir, e depois provei que não acontece mais.
2. **Dava para pendurar o Jarvis.** Mascarar texto custa por letra, e o corte do corpo vinha depois
   do mascaramento: 1 MB sem pontuação levaria mais de uma hora de processador com o turno travado.
3. **O mascaramento apagava o que a ferramenta existe para trazer.** Ao fechar um vazamento eu usei
   pedaços de nome largos demais, e `endpoint`, `D3_DOC` e `C1_CONTA` saíam como `***`.
4. **`file:///etc/...` no `.env`** abria arquivo da sua máquina em vez de recusar.
5. **Senha com nome fora do padrão** (`PROTHEUS_SENHA`, `TOTVS_PASS`) escapava do filtro e ia para o
   ambiente dos comandos; e o endereço do banco chegava aos trabalhadores de programação, que rodam
   sem o seu "s" — teste deles podia cair no banco de produção.

E um sexto, achado no meu próprio código novo da 0.19.0: **base64 pode conter `/`**, então um
cabeçalho `Authorization` ecoado num corpo de erro podia ser confundido com rota e sair no
resultado da busca. Agora nada que faça parte da credencial passa pela peneira.

---

## 2. Atalho com ícone (0.17.0 e 0.17.1)

`python -m jarvis --criar-atalho` põe o Jarvis, com ícone, na Área de Trabalho e no menu Iniciar.
Tecla Windows, escrever "Jarvis", Enter. `--remover-atalho` desfaz.

Dois cuidados que teriam dado errado em silêncio:

- **Quem informa onde fica a Área de Trabalho é o Windows.** Supor `C:\Users\voce\Desktop` falha com
  OneDrive ligado: o atalho iria para uma pasta que você não vê.
- **O instalador procura o Python que tem as bibliotecas.** Sua máquina tem quatro Python; o que
  responde por `python` é o da Loja da Microsoft, sem as bibliotecas, e o seu Jarvis vive no `.venv`
  do projeto. Sem essa busca, o atalho apontaria para o Python errado e você levaria dois cliques
  num ícone que não faz nada: sem janela, sem erro, nada.

### Defeito do painel achado no caminho

Rodando a bateria no Python do Windows, um teste do painel falhava uma vez a cada quatro. Não era
chiado do emulador: quando um pedido chegava sem informar o tamanho do conteúdo, o painel recusava
certo, mas fechava a conexão com dados por ler — e o Windows corta a conexão nessa hora, então a
recusa não chegava a quem mandou. O navegador veria "conexão perdida" em vez do motivo. Corrigido;
depois disso, oito rodadas seguidas limpas.

---

## 3. Conferidor do `.env` e o `/ajustes` (0.18.0)

Na partida, e a qualquer momento com `/ajustes`, o Jarvis lista as linhas do `.env` que **não
reconhece**. Nome trocado não faz nada, e antes disso ele ficava calado:

```
[.env] linha 12: ELEVENLABS_API_KEY não é um ajuste que eu conheça, então não faz nada.
       Você quis dizer JARVIS_ELEVENLABS_KEY?
```

Também avisa quando a mesma variável aparece duas vezes. **Nenhum valor aparece no aviso** — só o
nome e a linha, porque isso vai parar em captura de tela.

`/ajustes` mostra ainda o que está ligado na sessão: voz da nuvem, Protheus, terminal, painel,
palavra de ativação — sem mostrar chave nenhuma.

Um teste trava isso para o futuro: se alguém acrescentar um ajuste novo no código sem pôr na lista
do conferidor, a bateria quebra.

---

## 4. Menos restrição (0.18.0)

A regra do turno limpo virou ajuste seu:

```
JARVIS_TURNO_LIMPO=nao
```

Com ela solta, ele grava na memória e age no mesmo turno em que leu uma página ou um arquivo, sem
esperar você falar de novo. Era essa regra que fazia "lê essa página e salva o resumo" custar duas
falas.

**O que você perde:** um texto que ele leia — página, e-mail, registro do ERP — passa a conseguir
disparar ação sem passar por você. O resto do guarda continua de pé: pastas liberadas, lista de
ferramentas e limite de cliques dentro da página.

Três coisas **não** foram soltas, porque são regra sua e não minha: o `s` digitado para comando de
terminal, o seu OK para instalar skill de terceiros, e o Protheus só de leitura.

---

## 5. A voz da ElevenLabs (0.18.1)

O sintoma era "a voz não muda". A causa: o ID da voz no `.env` tinha 22 caracteres, e os da
ElevenLabs têm 20 — dois vieram a mais na cópia. A chave estava perfeita e a conta intacta.

O defeito era meu, em três camadas:

1. **O código aceitava de 15 a 40 caracteres como ID** e nunca conferia contra a sua biblioteca.
   Agora exige os 20 exatos e confere.
2. **Um ID errado derrubava toda frase para a voz local, para sempre**, e só o `/nuvem` contava o
   porquê. Agora, no primeiro 404, ele troca para a primeira voz da sua biblioteca e avisa.
3. **Voz inexistente desligava a nuvem inteira.** Ajuste errado não vale parar o recurso: usa a
   primeira e conta o que houve, listando os nomes das suas vozes.

Você não precisa mais do ID: pode pôr o **nome** da voz como aparece na sua biblioteca, ou deixar a
linha vazia para a primeira.

---

## O que falta, e o que é com a consultoria

1. **Rodar `/protheus`** na sua máquina. É o teste que decide se o Jarvis alcança o seu ERP — daqui
   eu não tenho caminho de rede até ele, então tudo foi verificado contra um Protheus de mentira
   que imita o de verdade. Três respostas possíveis:
   - funcionou — pode pedir os campos do MATA020;
   - **401** — com `SECURITY=1` no appserver, o REST exige token OAuth2 em vez de usuário e senha;
   - **404 ou falha de conexão** — o REST não está publicado nesse endereço ou porta.
2. **O nome do parâmetro de filtro.** Monto como `FILTER=A2_CGC eq '...'`, que é a forma corrente,
   mas isso varia com a versão do framework publicado. Se voltar 400 ou vier ignorado, é trocar a
   forma — a validação não muda.
3. **Pedir um usuário do Protheus só de consulta.** O Jarvis não escreve no ERP de jeito nenhum,
   mas um usuário sem permissão de escrita protege mais que qualquer regra que eu ponha no código.
