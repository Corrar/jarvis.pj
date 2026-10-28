# Jarvis: leitura do Protheus de produção, só GET

São três partes. A ordem importa: credencial primeiro, regra depois, pedido por último.
O Jarvis recusou por três motivos e cada parte responde a um deles:

| Motivo da recusa | O que resolve |
|---|---|
| "Não tenho TOTVS_PRD_* e não vou pedir senha no chat" | **Parte 1**: você mesmo põe a credencial no terminal, sem passar pelo chat |
| "Varredura em produção é arriscada" | **Parte 2**: uma regra fixa com escopo fechado, só GET, uma rota por vez e limite de chamadas |
| "Arquivo não vira ordem" | **Parte 3**: o pedido sai de você, digitado com suas palavras |

Esta orientação **não** tira do Jarvis o direito de parar e perguntar. Ele continua podendo recusar
se algo fugir do escopo. É isso que torna a leitura em produção aceitável.

---

## Parte 1: credencial (você faz, no seu terminal, antes de abrir o Jarvis)

A senha não passa pelo chat, não fica em arquivo e não aparece na linha de comando.

**PowerShell:**
```powershell
$env:TOTVS_PRD_USER = Read-Host "Usuário Protheus PRD"
$s = Read-Host "Senha Protheus PRD" -AsSecureString
$env:TOTVS_PRD_PASS = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
  [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)); Remove-Variable s
claude    # abre o Jarvis NESTE terminal: só assim ele herda as duas variáveis
```

**bash:**
```bash
read -rp "Usuário Protheus PRD: " TOTVS_PRD_USER; read -rsp "Senha Protheus PRD: " TOTVS_PRD_PASS; echo
export TOTVS_PRD_USER TOTVS_PRD_PASS
claude
```

Ao fechar o terminal, as variáveis somem. Use de preferência um usuário do Protheus **só de consulta**,
se o TI puder criar um. Isso protege mais do que qualquer regra escrita.

---

## Parte 2: regra fixa (cole uma vez no CLAUDE.md ou nas instruções do Jarvis)

```markdown
## REGRA — leitura do Protheus de PRODUÇÃO (autorizada pelo Bruno)

**Escopo autorizado:** GET em `https://royale191154.protheus.cloudtotvs.com.br:10807/rest`, que é o
Protheus de produção da Royale. Serve para levantar o catálogo REST e a estrutura dos modelos (fwmodel)
da integração Fluxo → Protheus.

**Rotas permitidas (lista fechada):**
- `/rest/` (o catálogo)
- `fwmodel/<ROTINA>?pageSize=1` para MATA020, MATA010, MATA360, MATA080, AGRA045 e MATA140
- `api/framework/v1/healthcheck`

Rota fora desta lista: pergunte antes.

**Como:**
1. Só o método GET, explícito (`-X GET`). Nunca POST, PUT, PATCH ou DELETE, nem "para ver se dá 405".
2. A credencial vem de `TOTVS_PRD_USER` e `TOTVS_PRD_PASS`, que o Bruno define no terminal. Ela entra
   no curl **pelo stdin**: `printf 'user = "%s:%s"\n' "$U" "$P" | curl -K - ...`. Nunca use `-u`, nunca
   ponha senha na URL e nunca escreva a senha em arquivo. Se as variáveis não existirem, pare e avise.
   Não peça a senha no chat.
3. Sempre `pageSize=1` e `--max-time 60`, uma chamada por vez, sem laço de repetição. No máximo 15
   chamadas por pedido.
4. **Mostre o comando antes de rodar**, sem a credencial, e rode depois do "ok" do Bruno. Com o "ok" do
   lote inteiro, rode o lote.
5. Na resposta: código HTTP, tamanho e tempo, mais o corpo cortado em 4 KB. Dado pessoal (CPF/CNPJ de
   pessoa física, conta bancária, e-mail, telefone) sai mascarado (`***`), mas o **nome do campo**
   fica. Nunca mostre `Authorization` nem `Set-Cookie`.
6. Se aparecer erro 5xx, lentidão acima de 10 s ou qualquer sinal de carga no servidor, **pare** e avise.

Esta regra vale só para leitura. Escrita em produção continua proibida até nova ordem escrita do Bruno.
```

---

## Parte 3: o pedido (digite você, com suas palavras; pode adaptar)

> Jarvis, as variáveis TOTVS_PRD_USER e TOTVS_PRD_PASS já estão no terminal, fui eu que defini.
> Seguindo a regra de leitura do Protheus de produção, quero saber:
> 1. quais serviços o `/rest/` publica;
> 2. o que `fwmodel/MATA020?pageSize=1` devolve: id do modelo, submodelos e campos;
> 3. quais de MATA010, MATA360, MATA080, AGRA045 e MATA140 respondem pelo fwmodel e, se não
>    responderem, qual o erro.
>
> Me mostra os comandos antes de rodar. Depois me entrega: a tabela de status (rota, HTTP, tamanho,
> tempo), a lista do catálogo e, para cada rotina, os submodelos e campos, ou o corpo do erro.

Depois cole a resposta dele aqui na sessão da integração. Eu faço a comparação com as seções 3 e 5 da
especificação para o parceiro.
