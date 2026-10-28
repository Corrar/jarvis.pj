# Calibração da MATA270 no Protheus pelo navegador (WebApp)

**Complemento da ESPEC-JARVIS-RPA-INVENTARIO-SB7 v1 · 23/10/2026**
**Para:** quem desenvolve o Jarvis, e o Bruno, que conduz a sessão.

## 1. O que muda por ser navegador

O Protheus aberto pelo navegador é o **SmartClient HTML (WebApp)**: a tela do Protheus é desenhada como
página web. Isso muda a etapa C para melhor.

| | Pela foto da tela (`janelas.py`) | **Pelo navegador controlado (Playwright, por exemplo)** |
|---|---|---|
| Como acha o campo | Pelo modelo, olhando a imagem | Pelo elemento da página (rótulo, ordem, atributo) |
| Lê de volta o que digitou | Por OCR ou pelo modelo | Pelo valor do elemento, exato |
| Erro do Protheus | Foto mais o modelo lendo | Texto da caixa de diálogo, exato |
| Tela bloqueada às 17:00 | Não funciona | Funciona com uma janela de navegador própria do Jarvis. Ainda sem tela invisível ("headless") em produção, ver §4. |
| Determinismo | Baixo | **Alto**: é o roteiro fixo que a 0.20 pediu |

**Recomendação:** a etapa C usa **um navegador próprio do Jarvis**, com perfil separado e sem as abas, os
logins e os cookies do Bruno, controlado por automação de navegador. Não usa o Chrome do dia a dia do
Bruno, nem a foto da tela.

⚠ **A confirmar na calibração.** O WebApp costuma montar a tela com componentes próprios (às vezes dentro
de *shadow DOM*) e identificadores que mudam a cada abertura. O roteiro tem de ancorar em **rótulo do
campo e ordem**, nunca em id gerado. Se o WebApp desenhar tudo num `canvas`, sem elementos legíveis, a
vantagem cai e voltamos à leitura por imagem. Isso é o primeiro a medir (passo C-0).

## 2. Antes da sessão

- [ ] Endereço do WebApp de **homologação**, aquele que o Bruno abre no navegador, e qual ambiente se
      escolhe na tela de entrada.
- [ ] Usuário do Protheus que o Jarvis vai usar (P-3), já com acesso à MATA270 em homologação.
- [ ] 3 produtos do Fluxo para lançar em homologação: um com saldo normal, um com saldo zero e um com
      SKU que talvez não exista no Protheus (testa o P-5).
- [ ] Jarvis 0.20 com `/inventario montar` funcionando (o CSV do dia ajuda a escolher os 3 produtos).
- [ ] Nada disso em produção.

## 3. A sessão (cerca de 40 minutos, o Bruno digitando e o Jarvis gravando)

O Jarvis abre o **navegador dele** em modo gravação e registra cada passo: o elemento, o rótulo, a tecla e
a espera. O Bruno faz o caminho normal, devagar.

| Passo | O Bruno faz | O Jarvis registra |
|---|---|---|
| **C-0** | Abre o endereço do WebApp | Se a tela é feita de elementos legíveis ou de `canvas`. **Se for `canvas`, para aqui** e o desenvolvedor reavalia. |
| C-1 | Tela de entrada: escolhe o ambiente e digita o usuário | Campos, ordem, botão de entrar. **A senha não é gravada.** Ela entra pelo `.env` na execução. |
| C-2 | Escolhe a empresa/filial **01 / 0101** e o módulo de Estoque | Onde aparecem o ambiente e a filial na tela. É a âncora da regra de segurança 1. |
| C-3 | Abre a MATA270 pelo menu, ou digitando o nome da rotina, se o WebApp permitir | O caminho e o título da tela, que é a âncora da regra 2 |
| C-4 | Clica em **Incluir** | O botão e a tela que abre |
| C-5 | Preenche documento, produto, armazém `02`, data e quantidade do **produto 1** | Cada campo: rótulo, ordem, tamanho, se é obrigatório e o que acontece ao sair dele (gatilho, consulta, aviso) |
| C-6 | **Confirma** | Se grava na hora (um item por inclusão) ou se volta para uma grade com confirmação no fim. Isso define a §6 da especificação. |
| C-7 | Repete com o **produto 2** (saldo zero) | Se o Protheus aceita zero ou avisa |
| C-8 | Repete com o **produto 3** (SKU duvidoso) | A mensagem exata de "produto não cadastrado", se vier. Isso responde o P-5. |
| C-9 | Tenta lançar **de novo o produto 1** com o mesmo documento | A mensagem de duplicidade |
| C-10 | Abre a consulta da SB7 filtrando o documento | Onde ler o que foi lançado, para a conferência da §10 |
| C-11 | Sai do Protheus | O caminho de saída. O Jarvis também mede quanto tempo de inatividade derruba a sessão do WebApp. |
| C-12 | (Opcional) Exclui os lançamentos de teste na própria MATA270 | Nada. A exclusão é do Bruno e **não entra no roteiro**. |

## 4. O que sai da sessão

1. **`roteiros/mata270.yaml`**: o roteiro fixo, ancorado em rótulos, com esperas e leitura de volta de
   cada campo. O Bruno revisa antes do primeiro uso.
2. **Catálogo de mensagens**: cada caixa que apareceu, marcada "normal" (segue) ou "erro" (cancela o item
   e para).
3. **Respostas pendentes da especificação**: um item por inclusão ou confirmação única (§6); campos
   obrigatórios e tamanhos (B7_DOC); se o B7_COD aceita o SKU (P-5); se zero é aceito; a mensagem de
   duplicidade.
4. **Decisão sobre tela invisível (headless)**: primeiro roda **com a janela visível**, em homologação,
   por uma semana (teste T9). Tela invisível em produção só depois disso, e só se o resultado for idêntico
   ao da janela visível.

## 5. Regras que a sessão NÃO muda

- A aprovação continua sendo `/inventario aprovar`, digitado.
- Etapa C só em `homologacao` até o aceite T1 a T9 da especificação.
- O acerto (MATA340) continua fora.
- A senha do Protheus não entra em gravação, log, captura de tela nem roteiro.
- O navegador do Jarvis só abre o endereço do WebApp configurado no `.env`. Qualquer desvio para outro
  endereço: para.
