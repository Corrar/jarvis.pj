# Jarvis: inventário semanal Fluxo → Protheus (SB7 + acerto)

**Versão 2 · 28/10/2026** · Substitui a etapa C da ESPEC-JARVIS-RPA-INVENTARIO-SB7 v1.
**Base:** o processo que o Bruno fez **à mão de 24 a 28/10/2026** em produção: 1.927 itens importados,
acerto rodado, 1 item recusado por empenho. Tudo aqui foi medido nessa rodada, não suposto.

---

## 1. O que muda em relação à v1

| v1 | v2 |
|---|---|
| Todo dia útil | **Toda sexta-feira** (`JARVIS_INV_DIAS=sex`) |
| Digitar item a item na MATA270 | **Importar o arquivo** pelo layout SB7 da própria MATA270 (Outras Ações > Outros > Importar). Foram 2.128 linhas em 14 minutos. |
| Só ALMOX, sem filtro | ALMOX **menos as exclusões** da §3 |
| Sem acerto | **Com acerto (MATA340)**, com a aprovação do Bruno logo antes do OK |

## 2. Etapa A: montar o arquivo (sexta, `JARVIS_INV_HORA`)

**Fonte:** `GET /stock` do Fluxo, que já está na 0.20.
- É a mesma lista do relatório "Catálogo completo" do front (`relatorio-estoque.csv`).
- A coluna **Física** do relatório é `quantity_on_hand`. Ela **já inclui o reservado** (Disponível + Reservado).
- Use **`quantity_on_hand`**. Nunca use o disponível.

**Data do inventário:** a da própria sexta (`DD/MM/AAAA`).

## 3. Exclusões (arquivo `inventario-exclusoes.json`, editável pelo Bruno)

Um produto **não entra** no arquivo se cair em qualquer regra abaixo. Os valores vêm das decisões do Bruno
em 24 a 28/10/2026.

```json
{
  "etiquetas": ["MIN-TAI", "INFORMATICA", "FEIRA", "VIAGEM", "FERRAMENTAS", "INSUMOS", "DESPESAS", "CAMISETA"],
  "prefixos_sku": ["FIL-PET"],
  "codigos_inativos_protheus": ["3.05.0115", "3.08.0113", "3.09.0338", "5.01.0250"],
  "codigos_sem_cadastro_protheus": []
}
```

- **Etiqueta:** compare sem diferenciar maiúscula de minúscula e sem acento (`INFORMÁTICA` = `INFORMATICA`).
  Um produto com **várias** etiquetas sai se **qualquer uma** estiver na lista.
- **`codigos_sem_cadastro_protheus`:** o Jarvis **propõe** acrescentar a essa lista os códigos que a
  importação recusar (§6), mas **só grava com o OK do Bruno**. Quando o produto for cadastrado no Protheus,
  o Bruno tira o código da lista.
- O relatório da etapa A mostra **quantos saíram por cada regra**, com a lista.

## 4. Formato do arquivo (o layout SB7 que funcionou)

`.xlsx` com uma planilha `Plan1` e cabeçalho na linha 1:

| Produto | Armazem | Documento | Quantidade | Data |
|---|---|---|---|---|
| `5.01.0255` (texto) | `02` (texto) | um por linha, ver abaixo (texto) | número | data `DD/MM/AAAA` |

- O CSV que vai para o Protheus é o **"Salvar como" do Excel** a partir desse `.xlsx`, no mesmo tipo usado
  em 24/10. É o caminho que funcionou. Se o Jarvis gerar o CSV direto, ele tem de ser **igual byte a byte**
  ao que o Excel gera. Conferir na calibração.
- **Documento:** **um número por linha**, nunca o mesmo documento para todas (essa tentativa falhou).
  Regra: `AA` + `SS` (semana ISO) + sequência de 4 dígitos. Exemplo: a semana 43 de 2026 vai de `26430001`
  a `2643NNNN`. São 8 caracteres, como os `2410xxxx` que o Protheus aceitou, e não repetem entre semanas
  nem entre anos.
- **Antes de importar**, o Jarvis confere no browse que **não existe nenhum documento da faixa da
  semana**. Se existir, para. Em 26/10, uma faixa reaproveitada (6000xx) misturou o teste com um
  inventário antigo.

## 5. Etapa B: importar na MATA270 (pelo navegador, depois do OK do Bruno)

Aprovação `/inventario aprovar` (digitada), como na 0.20. Depois:

1. **Entrar:** data base = **a sexta do inventário**, grupo **01**, filial **0101**, módulo **04
   Estoque/Custos**.
2. **MATA270, parâmetros (F12).** Os valores ficam gravados da última vez, então **conferir todos, sempre**:

   | Parâmetro | Valor |
   |---|---|
   | Valida Existência do Produto? | **Não** |
   | Sugere inform. inventário? | **Não Sugere** |
   | Data p/ opção sel. autom.? | **data do inventário** |
   | Selecionar contagem por? | **Produto** |
   | Data da contagem? | **data do inventário** |
   | Contagem? | **1** |

3. **Outras Ações > Outros > Importar**, layout **SB7**, arquivo da semana, **Importar**.
4. A mensagem de fim ("Houveram erros… LOG") é **esperada** quando há código sem cadastro. Não é motivo para
   parar, porque a conferência da §6 é que decide.

## 6. Etapa C: conferir o que entrou (automático)

1. No browse, **Outras Ações > Imprimir Browse**, tipo **PDF**, Preview. O PDF abre no navegador: baixar.
   As opções **Planilha** e **Em Disco** gravam no servidor (`\SPOOL\`) e não chegam ao computador.
2. Extrair as linhas (produto, armazém, documento, quantidade) e cruzar **pelo documento** com o arquivo da
   semana. Resultado:
   - **não entraram:** código provavelmente sem cadastro ou inativo. Propor para a lista da §3.
   - **⚠ quantidade diferente:** em 26/10, **todas as quantidades com decimal entraram truncadas**
     (62,6 → 62; 0,6 → 0). O Jarvis **corrige cada uma com Alterar** na MATA270, lendo de volta o valor,
     e confere de novo. Enquanto houver diferença, **não segue para o acerto**.
   - **extras:** linha da faixa da semana que não está no arquivo. Para tudo.
3. Relatório da conferência para o Bruno: totais, listas e o que foi corrigido.

## 7. Etapa D: acerto (MATA340), com o Bruno

**O acerto não tem volta**: gera requisições e devoluções que mudam saldo e custo. O Jarvis **preenche e
confere os parâmetros, tira a foto da tela e PARA**. O **OK é do Bruno**, que pode ser a segunda aprovação
digitada (`/inventario acertar`), e só depois disso o Jarvis clica.

| Parâmetro | Valor |
|---|---|
| Data de Seleção | **data do inventário** |
| Centro de Custo | **101** |
| Mostra Lanctos. Contábeis | Sim |
| Aglutina Lanctos. Contábeis | Não |
| Do / Até o Produto | branco / `ZZZZZZZZZZZZZZ` |
| Do / Até o Armazém | **02 / 02** |
| Do / Até o Grupo | branco / `ZZZZ` |
| **Do / Até o Documento** | **a faixa da semana** (ex.: `26430001` / `2643NNNN`) |
| Considera os Empenhos? | Firmes |
| Atualiza Saldo do Fechamento? | Sim |

⚠ **A faixa de documentos fica gravada da última execução.** Em 28/10 ela estava em `700001`–`701384`, de
um inventário de junho. Com ela, o acerto não processaria **nada** desta semana. O Jarvis **sempre**
reescreve e lê de volta esse campo.

Depois do OK:
- O relatório **"Itens não Inventariados"** abre sozinho. Tipo **Via Spool**, porque "Em Disco" dá o erro
  `NODRIVER`. Baixar e anexar ao relatório da semana.
- Itens com ocorrência **SB2E** (empenhados) **não são ajustados**: em 28/10 foi o `3.06.0002`. O Jarvis
  só **avisa**. A solução é baixar o empenho ou requisitar para a OP, e isso não é inventário.
- **Nunca rodar o acerto duas vezes na mesma data.** Gravar em disco que o acerto da semana foi feito, como
  a uma-vez-por-dia da 0.20.

## 8. Conferência final e relatório da semana

- Ler o saldo de **3 itens de amostra** (os 3 de maior valor que tiveram ajuste) e comparar com o arquivo.
- Relatório da semana na pasta `Inventario-Fluxo\AAAA-MM-DD\`, contendo:
  - o arquivo enviado e as exclusões por regra;
  - a conferência da importação e as correções de decimal;
  - a foto dos parâmetros do acerto;
  - os "Itens não Inventariados";
  - a amostra de saldo.

## 9. Travas que continuam valendo (0.20)

- Só as rotas `POST /auth/login` e `GET /stock` no Fluxo.
- No Protheus, pelo navegador, só a MATA270 e a MATA340. Nenhuma outra rotina.
- Aprovação **digitada** antes de importar e de novo antes do acerto. O "sim" falado não vale.
- Ambiente, filial e data base lidos da tela e conferidos antes de cada etapa.
- Qualquer tela ou mensagem fora do roteiro faz o Jarvis parar e mandar a foto.
- A senha do Protheus nunca vai para log, foto ou relatório.

## 10. O que precisa de calibração (uma sexta, junto com o Bruno)

1. O caminho exato no WebApp: menu da MATA270 e da MATA340, e onde fica cada botão.
2. O tipo de CSV do "Salvar como" que o layout SB7 aceita, e **por que o decimal foi truncado** (vírgula
   contra ponto). Se o formato certo resolver, a correção com Alterar da §6 vira só uma rede de segurança.
3. O download do PDF do Imprimir Browse e do "Itens não Inventariados" pelo navegador controlado.
4. Se a data base pode ser trocada para a sexta pelo usuário do Jarvis. Se não puder, o Bruno decide qual
   data os movimentos do acerto devem levar.

## 11. Em aberto, com o fiscal

- **Centro de custo 101** e as opções **Empenhos = Firmes** e **Atualiza Fechamento = Sim** foram as usadas
  em 28/10. O fiscal confirma que ficam fixas.
- O saldo do Fluxo como **contagem semanal**: a decisão de 24/10 foi do Bruno. Vale registrar que fica
  semanal.
