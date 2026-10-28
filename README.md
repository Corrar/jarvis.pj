# Jarvis — assistente de desktop com painel, voz, palavra de ativação, controle do navegador, programação em segundo plano e terminal

Assistente de desktop que roda na sua máquina e usa a API da Anthropic com a sua chave.
Conversa por texto ou por voz, lembra do que você pede e do que você corrige, registra as conversas, abre sites, arquivos e programas, organiza arquivos, fecha abas e janelas e mexe dentro das páginas do navegador e de programas como Excel e Word: rola, toca vídeo, clica e digita. Ele atende pelo nome ("Jarvis, abre o YouTube") de qualquer janela, sobe junto com o Windows e fica escondido em um ícone perto do relógio; uma tecla de atalho também o chama, e um painel próprio mostra o que ele está fazendo e o quanto já aprendeu. Ele também programa nos seus projetos: você dá a tarefa, um trabalhador faz o serviço em segundo plano e o resultado chega em um ramo do git para você revisar. E roda comandos no terminal, cada um com a sua confirmação. Ordem dada é ordem executada, dentro de limites fixos.

## Instalar (Windows, PowerShell)

Precisa de Python 3.10 ou mais novo e de uma chave de API da Anthropic. Para programar nos projetos, precisa também do [Git para Windows](https://git-scm.com/download/win): é o git que separa o trabalho do Jarvis do seu código, e é o Git Bash que vem com ele que roda os comandos dos trabalhadores.

```powershell
cd $HOME\jarvis-desktop
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env        # cole a chave em ANTHROPIC_API_KEY
```

Se o PowerShell bloquear o `Activate.ps1`, rode antes: `Set-ExecutionPolicy -Scope Process RemoteSigned`.

Para atualizar a partir de um zip novo, extraia por cima com `Expand-Archive -Force` e rode de novo o `pip install -r requirements.txt`. O `.env`, a `.venv`, a pasta `.jarvis` e a pasta `memoria` não estão no zip e ficam como estão.

## Rodar por texto

```powershell
python -m jarvis
```

Digite a pergunta e Enter. Comandos:

- `/memoria` — lista os fatos, as preferências e as lições que ele guardou.
- `/painel` — reabre a janela do painel, se você a fechou.
- `/historico` — mostra as últimas conversas, com ferramentas usadas e custo.
- `/custo` — gasto estimado da sessão.
- `/trabalhos` — os trabalhos de programação; `/trabalhos <código>` mostra um por extenso.
- `/missoes` — as missões em segundo plano; `/missoes <código>` mostra uma por extenso, com o relatório.
- `/rotinas` — os lembretes e as tarefas marcados, com a próxima vez de cada um.
- `/bomdia` — repete o cumprimento do dia agora.
- `/minha-voz` — grava a sua voz para só ela chamar o Jarvis; `/minha-voz apagar` desfaz.
- `/nuvem` — testa a voz da ElevenLabs na hora e diz o que aconteceu (chave, voz, crédito).
- `/protheus` — mostra a configuração da leitura do Protheus e testa o healthcheck na hora.
- `/ouvido` — com a palavra de ativação ligada: o estado do microfone, a nota de cada chamado conferido com a sua voz e o que ele ouviu sem tomar por chamado.
- `/esconder` — no segundo plano, esconde o terminal.
- `sair` ou Ctrl+C — encerra.

## Rodar por voz

```powershell
pip install -r requirements-voz.txt
python -m jarvis --voz
```

- Enter com a linha vazia começa a gravar; Enter de novo para e envia.
- **Ctrl+Alt+J, em qualquer janela**, começa a gravar; Ctrl+Alt+J de novo para e envia. Um bipe agudo avisa que ele está ouvindo e um mais grave que parou. Não precisa voltar ao terminal.
- Digitar continua funcionando no mesmo modo.
- Qualquer entrada nova cala a resposta que estiver tocando.
- A gravação para sozinha depois de um minuto.

A tecla de atalho só existe no Windows. Para trocar a combinação, use `JARVIS_ATALHO` no `.env` (por exemplo `f9` ou `ctrl+shift+espaco`); `JARVIS_ATALHO=nao` desliga. Se outro programa já usa a combinação, o Jarvis avisa ao iniciar e segue sem ela. No modo texto, a mesma tecla traz o terminal para frente. Quando a ordem vem pela tecla de atalho, ele não devolve o foco ao terminal no fim: você continua na janela em que estava.

Na primeira vez ele baixa a voz (cerca de 60 MB) e o modelo de transcrição (cerca de 500 MB) para `.jarvis\`. Depois disso a voz funciona sem internet; o que continua precisando de rede é a chamada à API.

Para trocar a voz ou o modelo de transcrição, veja `JARVIS_VOZ` e `JARVIS_WHISPER` no `.env.example`. Se a transcrição demorar, `JARVIS_WHISPER=base` é mais rápido e erra um pouco mais.

### Voz da nuvem (ElevenLabs)

Com uma chave da ElevenLabs no `.env`, a fala sai pela voz que você escolher lá, bem mais natural que a local:

```
JARVIS_ELEVENLABS_KEY=sua_chave
JARVIS_ELEVENLABS_VOZ=Nome da voz      # ou o ID; vazio usa a primeira da sua biblioteca
```

- **O plano grátis rende uns 20 minutos de fala por mês** (10 mil créditos; o modelo Flash gasta meio crédito por letra). O Jarvis conta as letras que manda e para em `JARVIS_ELEVENLABS_LIMITE` (padrão 9.000) ou quando a própria API diz que o crédito acabou; daí até o mês virar ele fala com a voz local, e avisa uma vez. `/custo` mostra o gasto do mês.
- **A voz local nunca sai de cena.** Sem internet, com a API fora do ar ou lenta (mais de 6 s), a frase sai pela voz local na hora. Três falhas seguidas e ele fica cinco minutos só na local. Chave recusada: desligada até reiniciar, com aviso.
- **As frases fixas** ("Sim, mestre…") são sintetizadas uma vez e guardadas em `.jarvis\elevenlabs-frases\`: não gastam crédito de novo.
- **Latência:** a primeira frase de cada resposta espera a API (meio segundo a um segundo); as seguintes já vêm prontas enquanto a anterior toca.
- **O que sai da máquina:** o texto de cada frase falada vai para a ElevenLabs. A escuta (Whisper), a palavra de ativação e o reconhecimento da sua voz continuam locais.
- **Se nada mudou depois de pôr a chave:** digite `/nuvem` no terminal; ele testa a API na hora e diz o motivo (chave recusada, voz não encontrada, crédito, rede). Uma chave criada com permissões restritas na ElevenLabs funciona desde que tenha "Text to Speech"; sem a permissão de ler a conta ele só deixa de mostrar os créditos, e sem a de listar vozes é preciso pôr o ID da voz, não o nome.
- `JARVIS_ELEVENLABS_MODELO=multilingual` troca para o modelo mais natural (metade dos minutos); a velocidade da engrenagem do painel vale também na nuvem (de 0,7 a 1,2). Trocar a voz local no painel troca a reserva; a nuvem continua por cima.

## Chamar pelo nome

Com a voz ligada, o microfone fica aberto e o Jarvis atende quando ouve o próprio nome, em qualquer janela. Há dois jeitos, e os dois valem sempre:

- **"Jarvis."** Só o nome: ele responde em voz alta ("Sim, mestre. Com o que eu posso te ajudar?", "À disposição, senhor. Qual é a minha missão do dia?"…, alternando), e o que você disser em seguida é o pedido. Se você não disser nada em dez segundos, um bipe grave avisa que ele desistiu. Para trocar as frases, ponha as suas em `JARVIS_RESPOSTAS` no `.env`, separadas por `|`.
- **"Jarvis, abre o YouTube."** De uma vez só: um bipe avisa que ele entendeu, e ele faz.
- Também vale "ei, Jarvis", "ok, Jarvis", "ô, Jarvis" e "bom dia, Jarvis". O nome no meio de uma frase não chama, nem falar dele ("o Jarvis abriu errado").

O pedido termina quando você fica um segundo em silêncio, e tem um teto de quinze segundos contando o nome. A tecla de atalho, o Enter e o teclado continuam funcionando como antes.

### Interromper pela voz

Ele continua ouvindo enquanto fala e enquanto trabalha:

- **"Jarvis, para."** Cala a fala e larga o que ele estiver fazendo, inclusive um comando de terminal em andamento. Também vale "pare", "chega", "silêncio", "cancela", "pode parar", "para tudo" e "por favor, para". Isso não vai para a conversa nem gasta uma chamada à API. O que já tinha sido feito até ali continua feito: um arquivo já movido segue movido.
- **Só "Jarvis"**, no meio de uma resposta: ele se cala, responde "Sim?" e espera o pedido. Se havia um trabalho em andamento, ele continua, calado.
- **"Jarvis, …" com outro pedido**, no meio de um trabalho: ele diz "Um instante", termina o que estava fazendo sem narrar o resto, e atende o novo em seguida. Para trocar de assunto na hora, diga antes "Jarvis, para".
- **Se você falar por cima dele e ele não reagir**, diga só "Jarvis", espere o "Sim?" e então peça. Com a voz dele misturada à sua, a transcrição pode pôr palavras dele antes do nome, e nesse caso só "Jarvis" e "Jarvis, para" são aceitos: "deixa o Jarvis terminar", dito enquanto ele fala, não é um pedido.
- **O "para" vale mesmo se o nome se perder.** Dito por cima da voz dele, o "Jarvis" costuma sair errado na transcrição. Mas ele se cala para ouvir assim que a sua voz entra, e o que você diz depois disso chega limpo: se for só uma ordem de parar ("para", "chega", "pode parar"), ele para.
- **"Para" só é ordem de parar quando há o que parar**: ele falando ou trabalhando, ou recém-calado. Com ele quieto, "Jarvis, para" é um pedido como outro qualquer (parar o vídeo que está tocando, por exemplo) e vai para a conversa.
- **Para ser ouvido por cima da voz dele, a sua voz precisa chegar ao microfone mais alta que a dele**, umas duas a três vezes. Com fone de ouvido ou headset isso é sempre verdade. Com a caixa de som alta e perto do microfone, fale mais alto ou abaixe o volume; a tecla de atalho continua cortando a fala como antes.
- Se alguém fala alto por cima dele sem chamar, ele se cala um instante para ouvir, vê que não era com ele e continua de onde parou (uma frase comprida recomeça um pouco antes do ponto do corte; uma curta, do começo). Para não gaguejar a cada barulho, depois disso passa uns segundos sem se calar de novo, e esse tempo dobra a cada engano (um chamado de verdade continua valendo). O `/ouvido` conta quantas vezes isso aconteceu.
- Durante uma confirmação no teclado (o `s`), o microfone fica surdo: nada do que for dito conta.

### Se ele demora ou não ouve o nome

- **Chamou e ele não reagiu? Repita em seguida.** Cada chamado é conferido de novo assim que você para de falar, sem precisar esperar. Se ele já tinha respondido e você chama outra vez, ele responde outra vez.
- **`/ouvido`** mostra o estado do microfone, quanto tempo cada transcrição está levando nesta máquina e como as últimas falas foram escritas. Se o seu "Jarvis" aparece como outra palavra, acrescente essa grafia: `JARVIS_PALAVRA=jarvis;jarbas`. Uma letra trocada ele já aceita sozinho ("Jarves", "Jarviz").
- **Velocidade.** O tempo entre você dizer "Jarvis" e ele responder é meio segundo de pausa mais uma transcrição. O Whisper sempre processa uma janela de trinta segundos, por mais curta que seja a fala; para falas curtas o Jarvis entrega a ele só a fala e uma folga (dez segundos no mínimo), o que deixa cada transcrição de três a quatro vezes mais rápida com o mesmo acerto (medido com o modelo `small`: de 1,5 s para 0,4 s numa máquina de dois núcleos). Ao subir, ele ensaia com o próprio nome e avisa se a transcrição passar de um segundo e meio. Nesse caso, `JARVIS_WHISPER_ATIVACAO=base` no `.env` usa um modelo menor só para achar o nome: responde mais depressa e perde um pouco mais de chamados.
- **Ele responde assim que entende o nome**, desde que você não esteja falando naquele instante. Não espera terminar de tratar o som que se acumulou enquanto transcrevia: quem chama de novo por não ter ouvido resposta não atrasa a resposta.
- **Depois de falar bastante, ou com TV e música.** Faça uma pausa de um segundo antes de chamar, ou chame mais alto: o que não para de soar vira "fundo" (veja abaixo).
- **Se a fala dele trava (para um instante e continua).** Duas causas possíveis. A primeira era a caixa de som sendo aberta a cada frase, o que o Windows cobra com um silêncio entre uma frase e outra; desde a 0.14.2 a saída fica aberta e as frases saem emendadas. A segunda é a voz dele voltando pelo microfone mais forte do que ele esperava (caixa alta e perto do microfone, ou o "controle automático de ganho" do Windows mexendo no volume do microfone): ele acha que alguém falou por cima, cala para ouvir e continua. O `/ouvido` mostra quantas vezes ele se calou para ouvir e em quantas não era com ele; se for isso, abaixe a caixa, afaste o microfone dela ou desligue as melhorias de áudio do microfone nas configurações de som do Windows.

### Como funciona, e o que isso custa

- **Tudo acontece na sua máquina.** O som é cortado em trechos de fala. Meio segundo depois de uma fala curta parar (ou aos três segundos de uma comprida), o trecho é transcrito, aqui mesmo, para ver se começa com o nome. Se não começa, é descartado: não vai para o disco nem para a internet. Se começa, o que vem depois é o pedido, e só vira pedido o que a transcrição mais cuidadosa escrever com o nome no começo. Para a API vai só o texto do pedido, depois do nome. As últimas frases descartadas ficam na memória do programa para o comando `/ouvido` e somem ao fechar.
- **A voz dele não é tomada por gente.** Ele sabe o que está tocando na caixa de som e aprende com que volume isso chega ao microfone. Enquanto ele fala, só é ouvido quem passar bem acima disso. Se você aumentar bastante o volume da caixa, ou trocar o fone pela caixa, ele pode se interromper uma vez na resposta seguinte (ouve a própria voz mais alta do que conhecia, confere que eram as palavras dele e aprende). Caixa Bluetooth com atraso de meio segundo ou mais atrapalha essa conta: nesse caso prefira fone ou caixa com fio.
- **Com a tela bloqueada (Win+L), ele não atende.** Ao desbloquear, volta a atender. Se o Jarvis subir com a tela já bloqueada (o Windows faz isso depois de algumas atualizações), fica calado até você entrar. Para atender mesmo bloqueado: `JARVIS_OUVIR_BLOQUEADO=sim`.
- **Som de fundo.** O que conta é a diferença de volume entre a sua voz e a sala. TV, música ou conversa que não param viram "fundo" em uns dez segundos, e daí em diante só uma voz mais alta que elas chama. Com uma conversa quase no seu volume por perto, ele pode perder o chamado ou cortar o fim do pedido: chegue mais perto do microfone ou use a tecla de atalho.
- **Microfone que some.** Se o microfone não existe quando o Jarvis sobe (um fone Bluetooth que conecta depois), ou cai no meio (cabo, computador voltando da suspensão), ele tenta abrir de novo a cada poucos segundos; o ícone fica cinza riscado enquanto isso.
- **Teclado e mouse.** Estalos curtos, como teclas, não contam como fala nem como barulho da sala: dá para chamar enquanto digita.
- **Quem estiver por perto também pode chamar, até você cadastrar a sua voz.** Sem o cadastro (veja "Só a sua voz", abaixo), um colega, ou um vídeo que diga "Jarvis, …", é ouvido do mesmo jeito, e as ações comuns (abrir, fechar, mover, mandar para a Lixeira, clicar e digitar em uma página) rodam sem confirmação. Continuam exigindo o `s` no teclado: comando de terminal e instalação de skill. Para não ser ouvido, desligue o microfone no ícone, ou troque o nome por um menos óbvio em `JARVIS_PALAVRA`.
- **Processador e memória.** Silêncio não custa nada. Cada fala perto do microfone custa uma transcrição (o `/ouvido` mostra quanto ela leva). Numa conversa que não é com ele, só as três primeiras frases são conferidas; depois ele espera um segundo de silêncio para conferir de novo. O modelo de transcrição fica na memória o tempo todo (até cerca de 1 GB com o `small`).
- **Fone Bluetooth.** Com o microfone do fone aberto o tempo todo, o Windows passa o fone para o modo de chamada e a música perde qualidade. Use outro microfone, ou desligue a palavra de ativação.

`JARVIS_PALAVRA=nao` desliga: o microfone volta a abrir só quando você manda gravar.

### Só a sua voz

Digite `/minha-voz` no terminal: ele baixa um modelo de reconhecimento de locutor (uns 26 MB, uma vez só) e pede quatro frases suas, uma de cada vez (Enter, fala, Enter). Nenhum áudio é guardado: ficam só as "assinaturas" da voz, em `.jarvis\voz-do-dono.npz`. Daí em diante, a cada chamado e a cada pedido falado, a voz é comparada ao cadastro aqui mesmo na máquina (uns 50 ms), e o que não for a sua voz é ignorado: nem o "Sim?" sai. Um chamado seu aceito com folga entra no cadastro, e ele vai conhecendo a sua voz nas condições reais (o seu microfone, a sua sala).

- **Faça o cadastro no lugar e com o microfone de sempre**, sem TV nem música. Se trocar de microfone (do headset para a caixa, por exemplo) e ele passar a não atender, refaça o cadastro.
- **Se ele deixar de atender você**, o `/ouvido` mostra a nota de cada chamado (a sua voz costuma passar de 0,5; outra voz fica abaixo de 0,35; o limiar é 0,40). Num ambiente barulhento, baixe o limiar em `JARVIS_LIMIAR_DA_VOZ=0.35`; se ele atender outra pessoa, suba. `/minha-voz apagar` volta a atender qualquer voz; `JARVIS_SO_O_DONO=nao` faz o mesmo sem apagar o cadastro.
- **"Jarvis, para" não passa pela conferência**: parar é seguro, e a voz de quem manda parar costuma chegar coberta pela dele. Os pedidos digitados (terminal, painel) e a tecla de atalho também não passam: são seus por definição.
- **O que foi medido.** Com vozes sintéticas em português (três vozes do Piper), a mesma voz pontua de 0,50 a 0,90 contra o cadastro, inclusive dizendo só "Jarvis"; outra voz fica entre 0,0 e 0,35. Com ruído forte (relação sinal-ruído de 10 dB) a própria voz cai até 0,43. Não foi medido com vozes humanas de verdade nem com o seu microfone: por isso o limiar é ajustável e o cadastro aprende.
- **Limites.** Uma gravação da sua voz (um áudio seu tocado perto do microfone) passa: isso reconhece a voz, não a pessoa. E se a conferência falhar por um defeito (modelo que não carrega), o chamado vale, para você não ficar trancado para fora; o motivo fica em `.jarvis\jarvis.log`.

## Rotinas: lembretes e tarefas marcados

"Jarvis, me lembre de ligar para o dentista às três da tarde." "Todo dia útil às oito, abre o e-mail e me diz o que chegou." "Toda sexta às 18h, me lembre de fechar o caixa." Ele marca e cumpre sozinho na hora:

- **Lembrete**: na hora, ele fala o texto, mostra um cartão no painel e um aviso no ícone. Não gasta uma chamada à API.
- **Tarefa**: na hora, o texto vira um pedido, como se você o tivesse falado naquele momento, e ele faz o que faria se você pedisse (abre, pesquisa, conta). O pedido chega marcado como rotina: ele sabe que você pode não estar na frente do computador, cumpre sem perguntar nada e responde curto. Uma tarefa que precise do seu `s` no teclado (um comando de terminal) fica esperando a confirmação como qualquer outra.
- **Repetição**: uma vez só, todo dia, nos dias úteis, em dias da semana ou todo mês. "O que tenho marcado?" lista; "cancela o lembrete do dentista" desmarca. `/rotinas` mostra tudo no terminal, e ao subir ele imprime as de hoje.
- **Com o Jarvis desligado na hora**: ao subir, um lembrete de uma vez que ficou para trás é dito mesmo assim ("era para ontem às 15h"); uma tarefa de uma vez só vale até doze horas depois; uma rotina repetida que ficou mais de três horas para trás é pulada (a próxima vez vem de qualquer jeito). O que foi pulado aparece no terminal.
- **Só a sua fala marca ou desmarca.** As ferramentas de marcar e desmarcar são recusadas no turno em que ele leu uma página, um arquivo ou um relatório: nada vindo de fora consegue agendar uma tarefa para ele cumprir sozinho mais tarde. A agenda fica em `.jarvis\rotinas.json`, em texto.

## Bom dia

Na primeira vez do dia em que você aparece (o Jarvis subindo junto com o Windows, ou a tela desbloqueada), entre 5h e 14h, ele cumprimenta e conta o que importa, nesta ordem: o tempo de hoje na sua cidade (previsão de verdade, do Open-Meteo, sem chave), as três notícias mais importantes de tecnologia e duas ou três do mercado financeiro (pesquisadas na hora, citando o veículo), os seus compromissos do dia, os trabalhos de programação que terminaram desde ontem e uma frase para o dia. Os cartões do tempo e das notícias aparecem no painel.

- Ponha a sua cidade em `JARVIS_CIDADE` no `.env` (pode ser "Curitiba, PR"). Sem ela, ele pula o tempo e avisa uma vez.
- `JARVIS_BOM_DIA=nao` desliga; `sempre` vale em qualquer hora; `6-12` limita às horas que você quiser. `/bomdia` repete agora, e "Jarvis, me dá o resumo do dia" também.
- É um turno como outro qualquer: gasta uma chamada à API, com algumas pesquisas na web (o `/custo` mostra). A data do último cumprimento fica em `.jarvis\bom-dia.json`.
- O tempo também vale fora do bom dia: "Jarvis, vai chover hoje?", "como está o tempo em Lisboa?".

## Missões em segundo plano

"Jarvis, em segundo plano, pesquise os cinco melhores notebooks até cinco mil reais e me diga qual compensa." "Lê os relatórios da pasta Documentos e me resume o que cada um conclui." Um pedido longo de leitura e pesquisa vai para um segundo agente, que trabalha sozinho enquanto a conversa segue livre: você pode continuar pedindo outras coisas, pela voz ou pelo teclado. Quando a missão acaba, ele fala o começo do relatório (a resposta), o relatório inteiro fica no terminal (`/missoes <código>`) e o painel mostra a missão enquanto ela roda.

- **A missão só lê e pesquisa**: as pastas liberadas, a web, a previsão do tempo, a memória e as rotinas (só leitura) e o painel (cartões no fim). Nenhuma ação na máquina (abrir, mover, apagar, clicar, comando) e nenhuma gravação (memória, rotinas): se o pedido precisar disso, ela apura tudo e diz no relatório o que ficou para você fazer, na conversa.
- **Limites**: uma missão de cada vez; até US$ 1 por missão (`JARVIS_ORCAMENTO_MISSAO_USD`), 80 passos e 30 minutos. "Jarvis, para a missão" interrompe; o que já estava escrito fica no registro.
- **Só a sua fala inicia uma missão**: a ferramenta é recusada no turno em que ele leu página, arquivo ou relatório. E o relatório da missão é texto gerado a partir da web e de arquivos: ele conta, não obedece.
- O registro fica em `.jarvis\missoes.json` e `.jarvis\missoes\<código>.log` (o passo a passo e o prompt que o agente recebeu).

## Segundo plano: ícone perto do relógio e início com o Windows

```powershell
python -m jarvis --iniciar-com-windows
```

Esse comando faz duas coisas: registra o Jarvis para subir sozinho quando você entra no Windows, e já o sobe agora. Ele roda com voz, sem janela, com um ícone perto do relógio (se não aparecer, está na setinha `^`; arraste para a barra para deixá-lo sempre à vista). Subir leva alguns segundos, por causa dos modelos de voz: o ícone fica cinza enquanto carrega e azul quando está pronto.

- **Um clique no ícone** mostra o terminal do Jarvis, para você digitar quando não puder falar. Outro clique esconde. `/esconder` também esconde. Para só mandar um pedido por escrito, o painel basta: ele tem uma caixa de texto.
- **Botão direito** abre o menu: Mostrar ou Esconder o terminal, Abrir o painel, Microfone ligado, Iniciar com o Windows e Sair.
- **Cores do ícone:** azul, pronto; verde, ouvindo o seu pedido; laranja, trabalhando; vermelho, precisa da sua confirmação no teclado; cinza riscado, microfone desligado.
- **Quando ele precisa do seu `s`** (comando de terminal, instalação de skill), o terminal aparece sozinho, na frente, e some de novo depois da resposta.
- **Quando um trabalho de programação termina**, além de falar, ele mostra um aviso do Windows.
- O X do terminal fica desligado nesse modo, porque fechá-lo derrubaria o Jarvis. Para encerrar de verdade: Sair no menu do ícone, ou `sair` no terminal. Se ele não sair em dez segundos, um segundo Sair encerra à força.

Outras formas de ligar e desligar:

- Dois cliques em `iniciar.pyw`, na pasta do Jarvis, sobem o Jarvis escondido sem mexer no início do Windows. `python -m jarvis --segundo-plano` faz o mesmo a partir de um terminal.
- `python -m jarvis --nao-iniciar-com-windows` tira do início do Windows. O item do menu do ícone faz a mesma coisa.
- `python -m jarvis --voz`, como antes, roda no terminal em que você digitou, sem ícone. A palavra de ativação vale nos dois jeitos.

Só um Jarvis roda por vez em cada instalação: com ele no segundo plano, um segundo `python -m jarvis` avisa e sai.

O início com o Windows é uma linha em "Executar ao entrar" do seu usuário (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`), que chama o `iniciar.pyw`. Não instala serviço e não pede administrador. Aparece em Configurações → Aplicativos → Inicialização, onde também dá para desligar.

Se o erro for na subida (falta de biblioteca, chave ausente), ele aparece em uma caixa de mensagem do Windows, já que o terminal está escondido.

**No Windows 11 com o Windows Terminal como terminal padrão**, uma janela escondida pode nascer dentro do Windows Terminal, que só sabe minimizar. O Jarvis percebe e recomeça sozinho no terminal clássico do Windows, que some por inteiro; nessa troca uma janela pode piscar na tela ao subir. Se mesmo assim sobrar uma janela minimizada na barra de tarefas, ele continua funcionando igual; para ela sumir, escolha "Host do Console do Windows" em Configurações → Sistema → Para desenvolvedores → Terminal.

## Painel

Ao iniciar, o Jarvis abre uma janela própria (960 × 620) com o visual do "Jarvis Visual 3D": uma rede neural em três dimensões que gira no centro, o relógio no canto, a engrenagem dos ajustes e, embaixo, a conversa. A página segue o desenho original elemento por elemento; o que muda é de onde vêm os dados, que aqui são os do Jarvis de verdade.

**O que a rede mostra.** Não há estado escrito na tela: quem conta é o movimento.

- **em espera**: a rede gira devagar e respira; de vez em quando passa um "pensamento" por ela.
- **ouvindo**: ela reage ao volume do seu microfone.
- **entendendo, pensando, agindo**: gira mais rápido e os sinais correm pelas sinapses.
- **falando**: ela se deforma no ritmo da voz dele (o volume real de cada frase), e a resposta aparece embaixo palavra por palavra. No modo texto, ou com a voz desligada, no ritmo da leitura.
- **desligado** (o Jarvis fechou): a rede esmaece e a caixa de pedido trava.

Embaixo ficam a sua última fala ("VOCÊ — …") e a resposta dele. Resposta comprida mostra só o trecho que está sendo dito.

**Digitar um pedido.** O ponto de luz embaixo vira uma linha de texto com um clique, ou é só começar a digitar com a janela em foco. Enter envia, Esc fecha. É o mesmo que digitar no terminal, sem precisar abri-lo:

- Se ele estiver ocupado, o pedido espera a vez (até cinco na fila) e a página avisa.
- "para" (ou "chega", "cancela"…) digitado ali, com ele falando ou trabalhando, interrompe na hora.
- Os comandos do terminal (`sair`, `/custo`, `/ouvido`…) não valem no painel: lá tudo é conversa.
- O que exige o seu `s` continua exigindo o `s` no terminal, que aparece sozinho para isso.

**Cartões.** Peça para ele mostrar alguma coisa ("abre um painel com a previsão do tempo de Rinópolis", "mostra as notícias de tecnologia") e ele abre até três cartões no meio da tela, com os dados que pesquisou: clima com gráfico e previsão, notícia com manchete e link, listas, números. Dá para arrastar e fechar cada um; Esc (ou "Fechar terminais") fecha todos, e sozinhos eles somem em um minuto. Regras:

- Os dados são os que ele achou. Ele é instruído a não inventar número, manchete, fonte nem link, e a dizer quando não achou.
- A página não carrega imagem de fora: no lugar da foto da notícia fica um quadro com a descrição. O link só abre se você clicar, no seu navegador.
- Os trabalhos de programação em segundo plano também aparecem como cartões escuros, com o projeto, o passo de agora e o tempo. Um cartão de trabalho que você fechou só volta quando o estado desse trabalho mudar.
- Se a janela do painel estiver fechada quando ele for mostrar um cartão, ela é aberta.

**A engrenagem.** Cor (seis), tema (escuro ou claro), movimento (lento, médio, rápido) e voz. Os ajustes ficam guardados em `.jarvis/painel.json` e valem de uma sessão para a outra.

- **Voz** desliga e liga a fala dele. Desligada, ele não diz nada (nem o "Sim?" ao ouvir o nome) e responde só por escrito; continua ouvindo. O Jarvis avisa na partida quando sobe com a voz desligada.
- **Velocidade** vai de 0,6 a 1,6 vezes a natural da voz e vale da frase seguinte em diante.
- A lista de vozes mostra a voz padrão (a do `JARVIS_VOZ`) e as outras que já foram baixadas nesta máquina (cada voz usada alguma vez em `JARVIS_VOZ` fica em `.jarvis/vozes`). Trocar carrega a outra em um instante.
- Sem `--voz`, a chave aparece desligada e não faz nada.

**O quanto ele sabe.** A rede começa com 240 neurônios, como no desenho, e cresce com a memória: um neurônio a mais por anotação e mais alguns conforme as conversas se acumulam, até 420. Quando ele aprende algo, os neurônios novos saem do centro e a rede "pensa" por um instante.

Como funciona: o painel é uma página local (`jarvis/painel.html`, um arquivo só, com as fontes IBM Plex embutidas) aberta no Chrome em modo aplicativo (no Edge, se não houver Chrome), sem abas nem barra de endereço. O Jarvis conta a ela o que acontece por um servidor que só escuta nesta máquina e exige um código secreto no endereço, sorteado a cada execução. Por ele a página só manda duas coisas ao Jarvis: o pedido digitado e a troca de um ajuste da engrenagem (que só aceita os valores da própria engrenagem). Só a própria página consegue: o servidor confere de onde o pedido veio e recusa o de qualquer outra página aberta no navegador. Quem tem acesso à sua sessão do Windows pode digitar ali, como pode digitar no terminal. A página não busca nada na internet. Ela recebe as legendas, os cartões e a quantidade de anotações, não o conteúdo delas.

A janela do painel fica fora do controle de janelas: ele não a lista, não a vê, não clica nela e não a fecha a pedido. Ao encerrar o Jarvis, a janela fecha junto.

Para não abrir o painel: `JARVIS_PAINEL=nao` no `.env`, ou `python -m jarvis --sem-painel`.

## Ações

O que ele faz na máquina:

- **Abrir**: sites (só `http` e `https`, no navegador padrão ou no Chrome, Edge ou Firefox se você pedir), arquivos e pastas das pastas liberadas, e programas de uma lista.
- **Organizar arquivos**, só dentro das pastas liberadas: criar pasta, mover, renomear, copiar e mandar para a Lixeira.
- **Controlar o que está aberto** (só no Windows): fechar uma aba do navegador, reabrir a última aba fechada, trazer uma janela para frente, fechar uma janela e dizer o que está aberto.
- **Mexer dentro das páginas** (só no Windows): rolar, tocar e pausar vídeo e música, ver a página, clicar e digitar nela.

Por padrão ele executa direto e mostra o que fez:

```
você > organize meus pdfs
  · Glob: **/*.pdf
  · mover
  >> mover 2 itens:
       C:\Users\voce\Downloads\a.pdf  →  C:\Users\voce\Documents\PDFs\a.pdf
       C:\Users\voce\Downloads\b.pdf  →  C:\Users\voce\Documents\PDFs\b.pdf
jarvis > Movi os dois PDFs para a pasta PDFs.
```

Há um caso em que ele para e pede o seu "pode": quando, no mesmo turno, leu uma página da web, o conteúdo de um arquivo ou uma conversa antiga antes de agir. Nesse turno as ações ficam bloqueadas; ele diz o que pretende fazer e executa na sua resposta seguinte. É a proteção contra um texto lido mandar nele. Procurar um arquivo pelo nome e agir no mesmo turno não bloqueia.

### Abas e janelas

- "Feche a aba do Instagram" procura a aba pelo título em todas as janelas de navegador (Chrome, Edge, Firefox, Brave, Opera, Vivaldi). Se ela não estiver à mostra, ele passa pelas abas uma a uma até achar; você vê o navegador piscar na tela. Sem achar, não fecha nada e deixa cada janela na aba em que estava.
- "Feche essa aba", sem dizer qual, fecha a que está à mostra no navegador usado por último.
- "Reabra a aba" desfaz o fechamento.
- "Feche o bloco de notas" fecha a janela como um clique no X: se houver algo sem salvar, o programa pergunta a você. Com mais de uma janela do mesmo programa, ele só fecha todas se você pedir todas.
- Depois de mexer no navegador, ele devolve o foco ao terminal para você continuar digitando.

Para isso ele traz o navegador para frente e manda um atalho de teclado, como você faria. Para as abas são quatro atalhos fixos no código (Ctrl+W, Ctrl+PageDown, Ctrl+PageUp e Ctrl+Shift+T). Antes de cada atalho ele confere se a janela certa está em primeiro plano; se outra janela tomou a frente, não manda nada. Se o Windows se recusar a trocar de janela, ele dá dois toques na tecla Alt, que é o jeito conhecido de destravar isso.

Limites dessa parte: a aba é achada pelo título da página, então uma aba cujo título não contém a palavra pedida não é encontrada. Com quatro ou mais abas vizinhas de título idêntico a procura pode parar antes da hora, e numa janela com mais de umas 55 abas ele desiste antes de dar a volta. A janela onde o Jarvis roda fica fora do alcance dele, desde que ela esteja em primeiro plano quando ele inicia, que é o normal: você acabou de digitar o comando nela.

Listar as janelas conta como ler conteúdo externo (títulos de páginas são texto de fora): no turno em que ele listou, só age depois do seu "pode".

### Dentro da página

- **Rolar**: "desce", "rola o feed do Instagram", "sobe duas telas", "volta ao topo". Ele usa a roda do mouse sobre a janela do navegador e devolve o ponteiro para onde estava.
- **Mídia**: "pausa", "continua", "próxima", "aumenta o volume". São as teclas de mídia do teclado: valem para o que estiver tocando, sem trocar de janela. O volume é o do computador.
- **Ver, clicar e digitar**: "toque o vídeo tal no YouTube", "clique no primeiro resultado", "pesquise tal coisa", "o que está aparecendo na tela?". Ele tira uma foto da janela do navegador, o modelo olha a foto e escolhe o ponto do clique. Depois de cada clique ele tira outra foto para conferir.

Como isso funciona e o que esperar:

- A foto é só da janela do navegador, encolhida para no máximo 1280 pontos de lado. Ela vai para a API da Anthropic, como tudo o que ele lê. Cada foto custa menos de um centavo de dólar no Sonnet; o `/custo` mostra o gasto real.
- O clique só sai se o ponto cair na janela fotografada, com ela em primeiro plano e no mesmo lugar. Se outra janela estiver por cima do ponto, ele não clica.
- Texto e teclas vão só para essa janela. As teclas avulsas são de uma lista fixa (Enter, Esc, Tab, espaço, setas e os atalhos do player do YouTube), sem Ctrl, Alt nem Win.
- É mais lento que rolar: cada passo é uma ida à API. Alvos grandes (miniatura de vídeo, botão) ele acerta bem; alvos miúdos podem pedir uma segunda tentativa.
- No fim da resposta o foco volta ao terminal, para você dar a próxima ordem. Deixe o terminal e o navegador lado a lado. Para o navegador ficar na frente, use `JARVIS_FOCO=navegador` no `.env`.

### Outros programas

"No Excel, digite 1500 na célula B3", "no bloco de notas, escreva a lista de compras", "o que está aparecendo no Word?". Funciona como no navegador: foto da janela, clique no ponto, texto e teclas.

- **Quais programas**: os da lista de abrir (calculadora, bloco de notas, paint, Excel, Word, PowerPoint, Outlook e os que você pôs em `JARVIS_PROGRAMAS`), mais os executáveis que você listar em `JARVIS_PROGRAMAS_TELA`, no `.env` (por exemplo `JARVIS_PROGRAMAS_TELA=spotify;vlc`).
- **Sempre de fora**, mesmo que você liste: terminais (Prompt, PowerShell, Windows Terminal), VS Code e outras IDEs, o Explorador de Arquivos, Editor do Registro, Gerenciador de Tarefas, Configurações, acesso remoto e cofres de senha. Digitar em um terminal seria rodar comando, e dois cliques no Explorador abrem qualquer arquivo. A lista está em `NUNCA_NA_TELA`, no `jarvis/janelas.py`.
- **Um programa por ordem sua**: a janela escolhida vira o alvo daquela resposta e ele não troca de programa no meio. E só vai a um programa se for o primeiro passo: depois de ler uma página, um arquivo ou outra janela, fica bloqueado até você falar de novo. O que uma página mostrar não leva o Jarvis ao seu Excel.
- **Diálogos do programa** (Salvar como, avisos) contam como a janela dele.
- **Teclas**: além das de antes, delete, F2, F5 e cinco atalhos de edição com nome fixo: salvar, desfazer, copiar, colar e selecionar tudo.
- **Senha**: janela com "senha" ou "password" no título não é vista.

Agora ele consegue alterar o conteúdo de um documento, digitando na janela como você faria. Ele só salva se você pedir. Aplicativos da loja do Windows (como a Calculadora nova) aparecem com o executável `applicationframehost`; para operá-los, ponha esse nome em `JARVIS_PROGRAMAS_TELA`.

O que ele não faz dentro da página:

- **Banco, pagamento e senha**: se o título da aba tiver palavras como banco, Nubank, Itaú, Pix, pagamento, checkout ou senha, ele não fotografa, não clica e não digita. A lista está em `PAGINAS_RESERVADAS`, no `jarvis/janelas.py`. A conferência é pelo título: uma página dessas com título neutro passa, então não peça para ele mexer no navegador com esse tipo de página à mostra.
- **Sair do navegador**: depois de ver uma página, abrir site, abrir programa, mexer em arquivo, fechar aba ou janela e gravar na memória ficam bloqueados até você falar de novo. O que uma página mostrar não consegue, sozinho, sair de dentro do navegador.
- **Ficar clicando sem parar**: são no máximo 40 ações de página por ordem sua.

O risco que sobra, dito sem rodeio: dentro do navegador você está logado nas suas contas, e ele clica e digita sem pedir confirmação. A persona manda fazer só o que você pediu e nunca obedecer a texto da página, mas isso é instrução ao modelo, não uma trava. Um texto malicioso em uma página que ele esteja vendo pode tentar convencê-lo a clicar onde não devia. Se preferir não correr esse risco, `JARVIS_TELA=nao` desliga ver, clicar e digitar e deixa só rolar e mídia, que não leem a página; ou `JARVIS_CONFIRMAR=sempre` faz ele pedir o seu `s` a cada clique.

### Limites

Como não há pergunta, o que protege você são os limites que ele nunca passa:

- Apagar de verdade: apagar manda para a Lixeira, de onde dá para restaurar.
- Sobrescrever: se o destino existe, o item é recusado.
- Abrir executáveis e scripts (`.exe`, `.bat`, `.ps1`, `.lnk`, `.msi`…), venham de onde vierem.
- Abrir programa fora da lista, ou passar a um programa qualquer coisa além de um endereço de site a um navegador.
- Mexer na pasta do próprio Jarvis (código, `.env`, memória), mesmo que ela esteja dentro de uma pasta liberada.
- Rodar um comando sem o seu `s` digitado: todo comando de terminal aparece antes na tela e espera a sua confirmação (veja Terminal). Editar o conteúdo de arquivos, só os trabalhadores de programação, presos à cópia de um projeto cadastrado.
- Clicar, digitar ou mandar teclas fora da janela do navegador ou de um programa liberado. As únicas teclas que valem para o sistema todo são as de mídia e o toque de Alt para trocar de janela.
- Clicar ou digitar em janelas de terminal, no Explorador de Arquivos, em ferramentas do sistema, acesso remoto ou cofre de senha.
- Mandar combinações livres: os atalhos de aba são fixos no código, e o modelo só escolhe teclas de uma lista com nome, sem Alt nem Win.
- Encerrar um programa à força: fechar janela é um pedido ao programa, que pode perguntar antes.

Tudo o que ele fez fica no `/historico`, item a item.

A lista de programas já traz calculadora, bloco de notas, explorador de arquivos, paint, chrome, edge, firefox, excel, word, powerpoint e outlook. Acrescente outros em `JARVIS_PROGRAMAS`, no `.env`.

**Se preferir aprovar cada ação**, ponha `JARVIS_CONFIRMAR=sempre` no `.env`. O terminal passa a mostrar a ação e só executa com `s` ou `sim`; qualquer outra resposta, ou 90 segundos sem resposta, cancela.

## Inventário diário Fluxo → Protheus

Todo dia útil às 17:00 (ajustável), o Jarvis lê o estoque do Fluxo Royale, monta a lista do
inventário no formato da SB7 e grava em `Documentos\Inventario-Fluxo\AAAA-MM-DD\`: `lista.csv`
(separador `;`, decimal com vírgula), `execucao.json` e `relatorio.md`. `/inventario` mostra a
situação, `/inventario montar` roda na hora, `/inventario aprovar` libera a digitação.

**A digitação na MATA270 ainda não existe**, de propósito: sem o roteiro calibrado da tela não há o
que digitar, e errar ali escreve lançamento torto no inventário de produção. Ele monta a lista,
avisa e para, dizendo o que falta.

O que protege, pelo código:

- **Duas rotas do Fluxo, fixas:** `POST /auth/login` e `GET /stock`. Nada mais sai daqui, e o
  modelo não participa: o inventário não é ferramenta dele, roda pelo relógio e pelo seu comando.
- **Sem seguir desvio**, no login e na leitura: seguir um 3xx mandaria a senha do Fluxo para o
  endereço que o servidor escolhesse.
- **O token não fica guardado** e a senha nunca aparece em mensagem, relatório ou log.
- **Barreiras antes de qualquer tela:** falha de login, estoque vazio, SKU vazio, repetido ou com
  caractere estranho, quantidade negativa ou não numérica, lista acima do teto.
- **Uma vez por dia, gravado em disco:** reiniciar o Jarvis não devolve o direito de lançar.
- **O "sim" é digitado ou clicado, nunca só falado.** Ele pergunta pela voz; a resposta vem pela
  mão. Televisão, colega na sala e o próprio alto-falante produzem um "sim"; um `s` digitado não.
- **O acerto (MATA340) não é feito**, e o relatório lembra disso todo dia: é com o fiscal.

## Calibrar a MATA270 no navegador

Antes de o Jarvis poder digitar inventário, ele precisa aprender a tela. `/calibrar` abre uma
**janela própria dele** — perfil separado, sem as suas abas, seus logins e seus cookies — no
endereço de `JARVIS_PROTHEUS_WEBAPP`, e grava o que você faz:

1. `/calibrar` — abre e mede a tela. Se ela for desenhada num `canvas`, ele **para aí** e diz por
   quê: sem elemento legível não há o que ancorar.
2. Você faz o caminho normal, devagar. `/calibrar` de novo recolhe o que já foi feito.
3. `/calibrar fim` — salva `roteiros/mata270.yaml` e fecha a janela.

O roteiro sai ancorado em **rótulo e ordem**, nunca em id gerado: o WebApp remonta id a cada
abertura, e roteiro preso a id quebra na segunda execução sem avisar. **A senha não entra no
arquivo** — o campo entra, o conteúdo não.

Precisa de uma biblioteca a mais, que você instala quando for calibrar:

```powershell
cd $env:USERPROFILE\jarvis-desktop; .\.venv\Scripts\python.exe -m pip install -r requirements-calibracao.txt; .\.venv\Scripts\python.exe -m playwright install chromium
```

A janela fica presa ao endereço configurado: qualquer tentativa de ir para outro host é recusada
antes de acontecer, e a sessão para. Ela também sobe sem conversa de fundo com a internet
(sincronização, telemetria, autopreenchimento) — é uma janela com a sessão do ERP aberta.

## Quando ele reclama do `.env`

Na partida e em `/ajustes`, o Jarvis lista as linhas do seu `.env` que ele **não reconhece** — nome
trocado não faz nada, e antes disso ele ficava calado. `JARVIS_ELEVEN_LABS_KEY` ou
`ELEVENLABS_API_KEY`, por exemplo, viram um aviso com o nome certo ao lado. Nenhum valor aparece no
aviso: só o nome da variável e a linha.

`/ajustes` também mostra o que está ligado nesta sessão — voz da nuvem, Protheus, terminal, painel,
palavra de ativação — sem mostrar chave nenhuma.

## Soltar a regra do turno limpo

Por padrão, depois de ler conteúdo de fora o Jarvis não grava na memória nem age até você falar de
novo. Isso custa uma fala a mais em pedidos como "lê essa página e salva o resumo". Para soltar:

```
JARVIS_TURNO_LIMPO=nao
```

O que você ganha: ele encadeia leitura e ação no mesmo turno. O que você perde: um texto lido —
página, e-mail, registro do ERP — passa a conseguir disparar ação sem a sua confirmação. O resto do
guarda continua de pé: pastas liberadas, lista de ferramentas e limite de cliques dentro da página.

## Abrir pelo atalho, com ícone

Para não precisar achar a pasta toda vez, uma vez só:

```powershell
cd $env:USERPROFILE\jarvis-desktop; python -m jarvis --criar-atalho
```

Ele põe o Jarvis, com ícone, na **Área de Trabalho** e no **menu Iniciar** — daí dá para apertar a tecla Windows, escrever "Jarvis" e dar Enter. Para deixar na barra de tarefas: tecla Windows, escreve Jarvis, botão direito, *Fixar na barra de tarefas*.

Se o `python` do seu PATH não for o que tem as bibliotecas do Jarvis, não tem problema: o instalador procura o certo (o do projeto, os que o lançador do Windows conhece) e aponta o atalho para ele. Se não houver nenhum, ele diz isso em vez de criar um atalho que não abre.

Quem diz onde fica a sua Área de Trabalho é o próprio Windows, não um palpite: com o OneDrive ligado ela não está em `C:\Users\voce\Desktop`, e o palpite criaria o atalho numa pasta que você não vê.

`python -m jarvis --remover-atalho` desfaz. O atalho abre o Jarvis em segundo plano, igual aos dois cliques no `iniciar.pyw` — é diferente de `--iniciar-com-windows`, que o liga sozinho quando você entra no Windows; os dois podem conviver.

## Ler o Protheus (só GET)

Para a integração do Fluxo Royale com o Protheus, o Jarvis consulta o ERP em vez de supor o dicionário: o catálogo do REST, o modelo (fwmodel) de uma rotina com seus submodelos e campos, e o healthcheck. Com estas linhas no `.env`:

```
JARVIS_PROTHEUS_URL=https://seu191154.protheus.cloudtotvs.com.br:10807/rest
JARVIS_PROTHEUS_USER=usuario.de.consulta
JARVIS_PROTHEUS_SENHA=sua_senha
```

Depois é só pedir: "Jarvis, o que o Protheus publica no REST?", "me mostra os campos do modelo do MATA020". `/protheus` confere a configuração e testa o healthcheck sem gastar um turno.

**O que protege o ambiente está no código (`jarvis/protheus.py`), não em recomendação ao modelo:**

- **Só GET.** O método é fixo; não existe no arquivo caminho para POST, PUT, PATCH ou DELETE. Escrever no Protheus o Jarvis não faz, nem se você pedir — isso continua sendo com a consultoria.
- **Paginação e filtro são números e listas, não texto livre.** `pageSize` vai de 1 a 50 e `page` de 1 a 1000, conferidos como número. O filtro é só por igualdade, num campo de lista fechada por rotina (hoje `A2_CGC` e `A2_COD` no MATA020), e o valor só aceita letra e dígito — sem espaço, aspas ou operador. Os dois limites juntos são o que impede isto de virar consulta livre ao banco de produção: o campo não é escolhido pelo modelo, e o valor não tem como sair das aspas.
- **A busca no catálogo não traz o corpo.** O catálogo é lido em fluxo, até 20 MB, e o que volta são as rotas que contêm os termos pedidos mais quantas existem no total. Antes o corpo vinha cortado em 4 KB, o que escondia justamente a rota procurada.
- **O modelo nunca passa uma URL.** Ele diz qual rotina quer, de uma lista fechada (`JARVIS_PROTHEUS_ROTINAS`, por padrão MATA010, MATA020, MATA080, MATA140, MATA360 e AGRA045). O caminho é montado aqui, sempre com `pageSize=1`. Rotina fora da lista é recusada em duas camadas — o esquema da ferramenta e o código — e nem chega à rede.
- **Desvio (3xx) não é seguido.** Quem responde não escolhe para onde vai a credencial. Sem isso, um proxy, um balanceador ou um portal de rede mal configurado na frente do Protheus recebe usuário e senha em texto claro — e o relatório ainda diria que leu do endereço certo. Um desvio para a leitura e manda você conferir o `JARVIS_PROTHEUS_URL`.
- **A credencial não passa pela conversa nem pela linha de comando.** Sai do `.env` direto para o cabeçalho `Authorization`, que não aparece em nenhum resultado. (`TOTVS_PRD_USER` e `TOTVS_PRD_PASS` também valem, se você já usa esses nomes.)
- **Teto de 15 leituras por fala sua** e de 120 no dia, uma por vez. Acabou a cota da fala, ele conta o que achou e espera você pedir o resto; sua próxima fala devolve as 15. Turno que você não pediu (rotina marcada, bom dia) leva só 5, e a conta do dia fica em `.jarvis/protheus.json`: fechar e abrir o Jarvis não devolve a cota do dia.
- **Para na primeira dor.** Erro 5xx, resposta acima de 10 segundos ou falha de rede travam as leituras até você falar de novo. Ele não insiste em produção.
- **Dado pessoal sai mascarado:** CPF, CNPJ, e-mail, telefone, banco, agência, conta bancária e endereço viram `***`, e o **nome do campo fica** — é dele que a integração precisa. O que decide é o *pedaço* do nome, não um trecho solto: `A2_CGC` e `A2_ENDCOB` saem mascarados, e `endpoint`, `D3_DOC` e `C1_CONTA` (conta contábil) aparecem inteiros, porque são justamente o dado que você precisa ver. Vale também quando a resposta não é JSON que dê para interpretar (XML com CDATA ou atributo, SOAP, HTML de proxy, ou resposta grande demais): aí o mascaramento é pelo nome do campo no próprio texto, e ele avisa que o corpo saiu reescrito. Corpo cortado em 4 KB, e o texto a mascarar em 8 KB — mascarar custa por letra, e um mega de resposta penduraria o turno.
- **A senha não escorre para outro lugar.** Ela está no `os.environ` do Jarvis, mas o `.env` dele não vai para o ambiente de nenhum comando de terminal (você aprova o comando, não o ambiente dele) nem para o shell dos trabalhadores de programação. Nome inequívoco de senha (`PROTHEUS_SENHA`, `TOTVS_PASS`, qualquer `*_PASSWORD`) também não passa, mesmo fora do nome documentado. O resto do seu ambiente continua de pé no terminal: um `gh` aprovado ainda tem o seu `GITHUB_TOKEN`, e um `npm run` ainda tem o seu `DATABASE_URL` — para o trabalhador, que roda sem o seu `s`, até o endereço do banco é cortado, para teste dele não cair no banco de produção.
- **O que vem de lá é informação, não ordem.** Depois de uma leitura, gravar na memória e as ações diretas ficam bloqueadas até a sua próxima fala (regra do turno limpo), então nada que esteja escrito num registro do ERP consegue, sozinho, mandar o Jarvis fazer algo.

Se vier **401**, pode não ser a senha: com `SECURITY=1` no appserver, o REST exige token OAuth2 em vez de usuário e senha — o Jarvis diz isso na resposta. Use de preferência um usuário do Protheus **só de consulta**, se o TI puder criar: isso protege mais que qualquer regra escrita.

## Programar em segundo plano

O Jarvis da conversa não programa. Ele entrega a tarefa a um **trabalhador**: outro agente, com as ferramentas de programação, que roda em segundo plano enquanto você faz outra coisa.

**Cadastre os projetos** no `.env`, com o nome que você vai falar e a pasta de cada um:

```
JARVIS_PROJETOS=Royale Vision=C:\dev\royale-vision;Painel=C:\dev\painel
```

Cada pasta tem de ser a raiz de um repositório git com pelo menos um commit. Reinicie o Jarvis depois de mudar o `.env`.

**Peça com as suas palavras:**

- "No Royale Vision, corrija o teste de login que está falhando."
- "No Royale Vision, crie a tela de relatório mensal; pronto é quando o build passar."
- "Como está o trabalho?" · "Para o trabalho." · "Como está o projeto Royale Vision?" · "Por onde continuamos?"

**O que acontece.** O Jarvis cria uma cópia do projeto em `.jarvis\copias\` (uma cópia de trabalho do git, em um ramo novo chamado `jarvis/<código>-<tarefa>`) e põe o trabalhador nela. A cópia parte do último commit do ramo em que o projeto está: o que você tem sem commit não vai junto, e a sua pasta e o seu ramo não são tocados. Ela começa só com o que está no git, sem dependências instaladas e sem `.env`; o trabalhador instala o que precisar ali dentro (`npm ci`, um `.venv`), roda testes, lint e build e faz commits assinados como "Jarvis".

**Acompanhar.** O painel mostra cada trabalho e o passo em que está. `/trabalhos` lista, e "como está o trabalho?" faz o Jarvis contar. Cada comando, cada arquivo alterado e cada recusa ficam em `.jarvis\trabalhos\<código>.log`.

**Quando termina,** o Jarvis avisa (por voz, se a voz estiver ligada) e o terminal mostra o relatório, o ramo e os commits. A cópia sai do disco; o ramo fica. Para revisar e aceitar, na pasta do projeto:

```powershell
git diff main...jarvis/a1b2c3-corrigir-login    # o que mudou
git merge jarvis/a1b2c3-corrigir-login          # aceitar
git branch -D jarvis/a1b2c3-corrigir-login      # ou descartar
```

Depois de aceitar, instale as dependências novas na sua pasta (`npm install`, por exemplo): o que o trabalhador instalou ficou na cópia dele. Dá para pedir os dois ao Jarvis ("aceita o ramo do último trabalho e instala as dependências"); cada comando aparece para você confirmar.

**Continuar.** Cada trabalho anota no diário do projeto (`.jarvis\diario\`) o que fez e o que ficou pendente, e o trabalhador seguinte lê esse diário. "Por onde continuamos?" faz o Jarvis ler o diário e propor o próximo passo; você confirma e ele inicia. Se o trabalho anterior ainda está em um ramo que você não aceitou, o novo pode partir dele.

**Limites de cada trabalho:**

- Pasta: lê e escreve só dentro da própria cópia. A pasta `.git` e o `.claude\settings.json` do projeto ele não altera.
- Comandos: só os de projeto. Instalar dependências no projeto, testes, build, lint, git local e utilitários de arquivo. Nada global (`npm -g`, `pip` fora de um `.venv`, `winget`), nada de `push`, `pull`, `fetch` ou troca de ramo, nenhum outro shell (`bash -c`, `powershell`, `cmd`), nada de `curl`, `ssh`, `docker` ou `gh`, nenhum servidor que fica rodando, nenhum caminho fora da cópia.
- Sem busca na web, sem subagentes, sem servidores MCP.
- Gasto: no máximo US$ 2 por trabalho (`JARVIS_ORCAMENTO_TRABALHO_USD`), 200 passos e uma hora. No limite ele para, e o que estava feito fica no ramo.
- Dois trabalhos ao mesmo tempo, no máximo.
- As chaves e senhas que estão nas variáveis de ambiente (a da Anthropic inclusive) são retiradas do ambiente dos comandos que ele roda.
- Se você fechar o Jarvis, os trabalhos em andamento param e o que estava feito fica no ramo.

**O que esse filtro é e o que não é.** Ele mantém o trabalhador nos trilhos e recusa o que não conhece, mas não é uma caixa fechada: quem pode rodar os testes do projeto roda o código que está no projeto, com os seus privilégios de usuário, e `npm install` roda os scripts dos pacotes. É o mesmo risco de você rodar esses comandos. O que protege o seu código é o trabalho ficar em um ramo à parte até você olhar. Leia o `git diff` antes do `git merge`, principalmente quando o trabalho mexeu em scripts, dependências ou configuração.

**Skills.** Os trabalhadores seguem o `CLAUDE.md` e usam as skills que o projeto traz em `.claude\skills`. As configurações do Claude Code que estiverem no git do projeto (`.claude\settings.json`, com os ganchos que você tiver posto lá) também valem para eles. Para instalar uma skill de fora, diga o endereço: "instala a skill pdf do repositório github.com/anthropics/skills". O Jarvis baixa, mostra no terminal o nome, a origem, os arquivos e os scripts que vieram e **só instala se você digitar `s`**, sempre, porque é código e instrução de terceiros que os trabalhadores vão seguir dentro dos seus projetos. Só repositórios públicos do GitHub, GitLab e Bitbucket. De plugins, ele aproveita as skills; ganchos e servidores MCP de plugin não são carregados. As instaladas ficam em `.jarvis\skills\`; "quais skills estão instaladas?" e "remove a skill pdf" funcionam.

**Outros ajustes no `.env`:** `JARVIS_MODELO_TRABALHO` (modelo dos trabalhadores; padrão, o mesmo da conversa) e `JARVIS_COMANDOS` (programas a mais que eles podem rodar, como `terraform;ffmpeg`).

## Terminal

O Jarvis roda comandos do PowerShell para você: "roda o git status no Royale Vision", "instala as dependências do projeto", "qual é o meu IP?", "aceita o ramo que o trabalho deixou".

**Como funciona.** Vale qualquer comando, em qualquer pasta, com os seus privilégios de usuário. Ele roda escondido, sem abrir janela, e o Jarvis conta o resultado. Em troca, **nenhum comando roda sozinho**: o terminal do Jarvis mostra o comando exato, a pasta e o limite de tempo, e ele só é executado se você digitar `s` e Enter.

```
  >> O Jarvis quer rodar um comando no terminal:
       Pasta: C:\dev\royale-vision
       Limite: 60 segundos, sem janela
       Comando:
         git status
     Confirma? [s/N]
```

Qualquer outra resposta, ou 90 segundos sem resposta, cancela. No modo de voz ele avisa que precisa da confirmação no teclado.

**O que protege a confirmação:**

- O que aparece é o que roda. Um comando com caractere invisível ou de controle é recusado antes de ser mostrado, e nada que o Jarvis escreve no terminal leva sequência de escape: uma página ou um arquivo não conseguem esconder nem redesenhar essa tela.
- Um `s` digitado antes de o pedido aparecer não vale. Se sobrou um Enter da confirmação anterior, ele é ignorado e o pedido continua esperando.
- Quando o Jarvis leu uma página, um arquivo ou a saída de outro comando no mesmo turno, a tela avisa: "Atenção: neste turno ele leu conteúdo externo". É o momento de conferir se o comando é mesmo o que você pediu.
- A saída de um comando conta como conteúdo de fora. Depois dela, gravar na memória e as ações diretas ficam bloqueadas até a sua próxima fala; outro comando continua possível, de novo com o seu `s`.

**Limites que valem mesmo com o seu `s`:**

- O comando roda sem teclado: se pedir alguma digitação, recebe vazio em vez de travar.
- Tem limite de tempo (60 segundos; o Jarvis pode pedir até 10 minutos). Ao passar, o comando e os programas que ele abriu são encerrados. Servidor que fica rodando (`npm run dev`) não serve aqui: peça "abre um terminal no projeto" e rode você mesmo na janela.
- A chave da Anthropic não vai para o ambiente do comando.
- O PowerShell é chamado sem o seu perfil (`-NoProfile`), então atalhos definidos no `$PROFILE` não existem ali.
- Clicar e digitar em janelas de terminal continua proibido para ele: comando, só por este caminho.

**Leia antes de confirmar.** A confirmação é a única barreira: não há lista de comandos proibidos. Um `s` em um comando que apaga, apaga. O Jarvis é instruído a dizer em voz alta quando o comando apaga, instala, encerra processos ou publica algo, mas quem decide é quem digita.

Tudo fica em `.jarvis\terminal.log`: cada comando, a pasta, se foi confirmado e como terminou. Para tirar essa capacidade do Jarvis, ponha `JARVIS_TERMINAL=nao` no `.env`.

## Memória

São três categorias, cada uma em uma pasta de arquivos de texto que você pode abrir, corrigir ou apagar em qualquer editor:

- **Fatos** (`memoria\`): o que ele sabe sobre você e as suas coisas. Diga "lembre que…" e ele guarda; diga "esqueça…" e ele apaga. Ele também guarda sozinho quando você conta um fato duradouro. No início de cada sessão o índice entra no prompt, e o texto completo ele lê quando precisa.
- **Preferências** (`memoria\preferencias\`): como você quer que ele trabalhe e responda, como "prefiro respostas curtas" ou "abra sempre no Chrome". Ele guarda quando você diz como gosta que algo seja feito. As preferências entram inteiras no prompt de toda sessão, e ele segue sem você repetir.
- **Lições** (`memoria\licoes\`): erros que ele cometeu e o que fazer no lugar. Quando você corrige o Jarvis ou reclama de uma resposta, ele grava uma lição curta. As lições entram inteiras no prompt de toda sessão, para o erro não se repetir.

Senhas e chaves são recusadas. A memória é texto puro no disco.

**Turno limpo.** Num turno em que ele leu a web, um arquivo, uma conversa antiga ou o relatório de um trabalho, salvar e apagar memória ficam bloqueados. Ele diz o que quer guardar e pede a sua confirmação; na sua fala seguinte, grava. Assim um texto malicioso numa página não consegue, sozinho, plantar uma lembrança ou uma "lição" permanente. A regra barra a gravação automática, não a persuasão: leia o que ele propõe guardar antes de confirmar.

Para criar outra categoria, acrescente uma linha em `CATEGORIAS`, no `jarvis/memoria.py`, e descreva na `persona.md` quando usá-la.

## Histórico

Cada turno fica em `.jarvis\registro.db` (SQLite): a pergunta, a resposta, as ferramentas chamadas com o que foi recusado ou não confirmado, a duração e o custo. O `/historico` mostra os últimos, e ele mesmo consulta esse registro quando você pergunta por uma conversa passada. Qualquer visualizador de SQLite abre o arquivo. É texto puro no disco: o que você perguntar fica lá.

## Camadas de proteção

| Camada | Onde fica | O que garante |
|---|---|---|
| Ferramentas disponíveis | `agente.py`, `FERRAMENTAS_NATIVAS` | O modelo só recebe Read, Glob, Grep, WebSearch e as ferramentas do Jarvis. Bash, Write e Edit do binário não existem para ele. |
| Modo de permissão | `agente.py`, `permission_mode="dontAsk"` | Toda chamada que pediria confirmação ao binário é negada, sem perguntar. |
| Regras de negação | `guarda.py`, `regras_de_negacao()` | Pastas e arquivos sensíveis (`.ssh`, `AppData`, `.env`, `*.pem`…) ficam fora da leitura e das buscas, em qualquer nível. |
| Guarda de caminho | `guarda.py`, `avaliar()` | Leitura fora das pastas liberadas é recusada, inclusive por `..` ou atalho simbólico. |
| Turno limpo | `guarda.py`, `Guarda` | Depois de ler conteúdo externo, a memória não é gravada e nenhuma ação roda até você falar de novo. |
| Validação das ações | `acoes.py` | Cada ação é conferida antes de rodar: pastas liberadas, sem sobrescrever, apagar só para a Lixeira, sem executáveis, programas só da lista. |
| Teclas fixas | `janelas.py`, `TECLAS_DE_PAGINA` | Atalhos de aba fixos no código e teclas de uma lista fechada, só para a janela em uso e só com ela conferida em primeiro plano. |
| Programas liberados | `janelas.py`, `NUNCA_NA_TELA` e `Janelas.operaveis` | Fora do navegador, só programas da lista; terminais, Explorador e ferramentas do sistema nunca. |
| Um alvo por turno | `janelas.py` e `guarda.py` | A janela de programa é escolhida em turno limpo e não muda até você falar de novo. |
| Clique conferido | `janelas.py`, `Janelas.clicar` | O clique exige uma foto deste turno, a janela no mesmo lugar e em primeiro plano, e nenhuma outra janela por cima do ponto. |
| Páginas reservadas | `janelas.py`, `PAGINAS_RESERVADAS` | Aba com título de banco, pagamento ou senha não é fotografada nem recebe clique ou texto. |
| Limite na página | `guarda.py`, `limite_na_pagina` | No máximo 40 ações de página por turno. |
| Confirmação digitada (opcional) | `cli.py`, `JARVIS_CONFIRMAR=sempre` | Com ela ligada, nenhuma ação roda sem o seu `s`. |
| Isolamento | `agente.py`, `setting_sources=[]` e `strict_mcp_config=True` | Ignora configurações, CLAUDE.md e servidores MCP do Claude Code instalados na máquina. |
| Programação à parte | `trabalhos.py`, `Oficina` | Quem programa é outra sessão, sem as ferramentas da conversa, sem web e sem MCP, com limite de gasto, de passos e de tempo. |
| Cópia e ramo próprios | `projetos.py` | O trabalho acontece em uma cópia de trabalho do git, em ramo `jarvis/...`. A sua pasta e o seu ramo não mudam; não há push, pull nem fetch. |
| Guarda do trabalho | `comandos.py`, `GuardaDoTrabalho` | Cada arquivo e cada comando do trabalhador é conferido antes: só dentro da cópia, só comandos de projeto. Na dúvida, recusa. |
| Tarefa só da sua fala | `ferramentas.py`, `SO_EM_TURNO_LIMPO` | Iniciar um trabalho ou baixar uma skill é recusado no turno em que o Jarvis leu página, arquivo, diário ou relatório. |
| Skill só com `s` digitado | `cli.py`, `confirmar_digitando` | Nenhuma skill de fora é instalada sem você ver o que veio e digitar `s`, com ou sem `JARVIS_CONFIRMAR`. |
| Comando só com `s` digitado | `ferramentas.py`, `terminal_executar` e `cli.py`, `confirmar_comando` | Nenhum comando de terminal roda sem aparecer inteiro na tela e receber o seu `s`. Recusado ou sem resposta, nada roda. |
| O que aparece é o que roda | `terminal.py`, `conferir_comando` e `cli.py`, `SaidaLimpa` | Comando com caractere invisível é recusado; texto de fora não leva sequência de escape ao terminal; `s` adiantado não vale. |
| Saída de comando contamina | `guarda.py`, `Guarda.contaminar` | Depois de um comando, memória e ações diretas ficam bloqueadas até você falar de novo. |
| Nome só no começo | `voz/vigilia.py`, `separar_pedido` e `Vigia._separar` | O chamado precisa começar com o nome, com no máximo uma letra de diferença. O nome no meio de uma frase não acorda o Jarvis. Uma exceção: numa fala que começou por cima da voz dele, o nome no meio vale para chamar a atenção ("Jarvis!") ou mandar parar, e para mais nada. |
| Pedido só da transcrição cuidadosa | `voz/vigilia.py`, `Vigia._ler` e `Vigia.tratar` | O texto que vai para a conversa é sempre o da transcrição mais cuidadosa. Dito de uma vez ("Jarvis, abre…"), ela precisa trazer o nome no começo. Dito depois do "Sim?", o pedido não repete o nome: vale a fala seguinte, até oito segundos depois. Com um modelo menor para achar o nome (`JARVIS_WHISPER_ATIVACAO`), é ele quem decide se alguém disse só "Jarvis". |
| Parar sem o nome, só por cima da voz dele | `voz/vigilia.py`, `Vigia._mandou_parar` | Uma ordem de parar sem o nome só vale numa fala que começou por cima da fala do Jarvis, só no que foi dito depois de ele se calar para ouvir (ali não há palavra dele), só se for a ordem inteira ("para", "pode parar", até quatro palavras) e só havendo o que parar. Parar só para: não confirma nem aprova nada. |
| A resposta dele não entra na fala de quem chama | `voz/vigilia.py`, `Cortador._quadro_novo` e `Vigia.tratar` | Se ele começa a falar com uma fala já em curso (o "Sim?" saindo enquanto a pessoa chama de novo), o que chega no volume dele é apagado dessa fala antes da transcrição. E um chamado repetido que traga junto a resposta dele ("Jarvis… Pois não?") é só um chamado repetido, não o pedido "pois não". |
| Janela curta da transcrição | `voz/escuta.py`, `Transcritor._transcrever_curto` | Usa peças internas da faster-whisper. Se elas faltarem ou falharem (outra versão da biblioteca), ele registra o erro e passa a usar só a transcrição comum, mais lenta. O que é silêncio ou barulho para o detector de voz não chega ao modelo, e um texto pouco provável na transcrição cuidadosa é refeito pela comum. |
| Pedido com teto | `voz/vigilia.py`, `Cortador` | Depois do nome, o pedido acaba no silêncio ou em quinze segundos: o que a sala disser depois não vai junto. |
| Surdo na confirmação | `voz/vigilia.py`, `Vigia` e `cli.py`, `Chamado.ouvir` | Durante uma confirmação no teclado o microfone não ouve nada, e vale o estado da hora em que o som entrou nele. Nada dito em voz alta vale como `s`. |
| A voz dele não é pedido | `voz/vigilia.py`, `Cortador` e `voz/fala.py`, `Falante.tocado` | Enquanto ele fala, só é ouvida uma voz bem mais alta do que a dele chega ao microfone. A primeira coisa ouvida depois do "Sim?", se for igual a ele, é descartada. O que ele mesmo diz, ouvido de volta, não vira pedido nem com o nome dele no meio. |
| Parar só para | `cli.py`, `Andamento` e `cumprir` | "Jarvis, para", com ele falando ou trabalhando, cala e interrompe aquele pedido; não confirma nem aprova nada, e não vai para a conversa. Um pedido novo no meio de outro espera a vez, na ordem em que chegou: não atropela o que está em andamento. |
| Painel só aceita a própria página | `painel.py`, `do_POST` | O pedido digitado e a troca de um ajuste só entram pelo endereço com o código secreto, vindos da própria página (mesma origem, corpo em JSON), com tamanho limitado. No pedido, sem caracteres invisíveis, e no máximo cinco esperam a vez. No ajuste, só os campos e os valores da engrenagem (`validar_aparencia`): qualquer outra coisa é recusada inteira. |
| Cartões do painel | `painel.py`, `limpar_paineis`; `ferramentas.py`, `painel_mostrar` | O que o modelo manda mostrar passa por uma peneira antes de chegar à página: no máximo três cartões, só os campos que a página desenha, cada texto no seu tamanho, ícone só da lista, link só http(s) e sem caracteres estranhos. Endereço de imagem é descartado: a página não busca nada fora (a política dela também proíbe). Tudo entra na página como texto, nunca como HTML. A ferramenta só mostra: não age na máquina. |
| Protheus só de leitura | `protheus.py`, `Protheus._texto` e `pedir` | O método GET é fixo no código e não há caminho para POST/PUT/PATCH/DELETE. O modelo não passa URL: diz a rotina, de uma lista fechada (conferida pelo esquema da ferramenta e de novo no construtor), e o caminho é montado com `pageSize=1`. A credencial vai no cabeçalho e não aparece em resultado, log nem mensagem de erro. |
| Desvio não é seguido | `protheus.py`, `_SemDesvio` | Um 3xx não é seguido: seguir mandaria usuário e senha para o host que o servidor escolhesse, em claro, e o relatório diria que leu do endereço certo. O desvio trava as leituras e manda conferir o endereço. |
| Teto e freio no Protheus | `protheus.py`, `MAX_CHAMADAS`, `MAX_POR_DIA` e `travado` | No máximo 15 leituras por fala do usuário (5 em turno automático) e 120 no dia, gravadas em `.jarvis/protheus.json` para o teto do dia sobreviver a reinício. Erro 5xx, desvio, resposta acima de 10 s ou falha de rede param as leituras até a próxima fala: não há insistência em produção. |
| Dado pessoal mascarado | `protheus.py`, `CAMPOS_SENSIVEIS`, `mascarar` e `mascarar_bruto` | Valor de CPF, CNPJ, e-mail, telefone, banco, agência, conta e endereço sai como `***` — no JSON, nas estruturas aninhadas, na chave do objeto e também em corpo que não dá para interpretar (XML, HTML, resposta cortada), onde o mascaramento é pelo nome do campo no texto. O nome do campo sempre fica. |
| Credencial do Jarvis fora dos shells | `terminal.py`, `DO_JARVIS` e `SEGREDO` | O `.env` do Jarvis (Anthropic, ElevenLabs, Protheus) não entra no ambiente de um comando de terminal nem no dos trabalhadores. O ambiente do usuário continua de pé no terminal; para o trabalhador, que roda sem o `s` dele, o corte é largo. |
| Tela bloqueada | `segundo_plano.py`, `Bloqueio` | Com a tela do Windows bloqueada, a palavra de ativação não é atendida. |
| Só a voz do dono | `voz/dono.py`, `VozDoDono.conferir` e `voz/vigilia.py`, `Vigia._e_o_dono` | Com a sua voz cadastrada, um chamado ou um pedido falado de outra voz é ignorado aqui mesmo, antes de ir à conversa (e fica marcado no `/ouvido`). Parar não passa por isso. Se a conferência falhar por defeito, o chamado vale. |
| Rotina só da sua fala | `ferramentas.py`, `SO_EM_TURNO_LIMPO` e `rotinas.py` | Marcar e desmarcar uma rotina é recusado no turno em que ele leu página, arquivo ou relatório. A agenda é texto na pasta de dados; o texto da rotina tem teto e a hora tem de ser futura. |
| Missão à parte e só de leitura | `missoes.py`, `Missoes` | Quem cumpre a missão é outra sessão, com as ferramentas de leitura, a mesma guarda de pastas e nenhuma ferramenta que age ou grava; limite de gasto, de passos e de tempo; uma de cada vez. Iniciar uma é só em turno limpo. |
| Bom dia e tempo sem ação | `bom_dia.py`, `clima.py` | O cumprimento do dia é um pedido montado pelo Jarvis, não lido de fora. A previsão vem de dois endereços fixos (https), com tempo de espera curto, e conta como conteúdo de fora: depois dela, ações só na sua fala seguinte. |
| Comandos só no terminal | `cli.py`, `_laco_da_conversa` | `sair` e os comandos com barra só valem digitados no terminal (ou ditados pelo atalho). Um "sair" ouvido pelo nome, ou digitado no painel, vai para a conversa e não desliga nada. |
| Um Jarvis só | `segundo_plano.py`, `Instancia` | Uma segunda cópia da mesma instalação não sobe: não há dois ouvindo o mesmo microfone. |

Pastas liberadas por padrão: `Documents`, `Desktop` e `Downloads` do seu usuário. Troque em `JARVIS_PASTAS` no `.env`.

## O que sai da máquina

- Tudo que o Jarvis lê para responder (trechos de arquivo, resultados de busca) é enviado à API da Anthropic. Libere só as pastas que podem ir.
- As lições e o índice dos fatos vão juntos em toda sessão, e o texto de um fato vai quando ele o consulta.
- Quando ele vê uma página, a foto da janela do navegador vai para a API. Nela aparece o que estiver na janela naquele momento.
- No modo de voz, o áudio não sai: a transcrição e a fala rodam aqui. Para a API vai só o texto transcrito.
- Com a palavra de ativação, o microfone fica aberto, mas o que não começa com o nome é descartado aqui mesmo, sem ir para o disco. Para a API vai só o pedido que veio depois do nome.
- O reconhecimento da sua voz roda aqui: o modelo é baixado uma vez (do GitHub, k2-fsa/sherpa-onnx) e o áudio não sai da máquina; só as assinaturas ficam no disco.
- Com a voz da nuvem ligada, o texto de cada frase falada vai para a ElevenLabs (api.elevenlabs.io).
- Com a leitura do Protheus ligada, o Jarvis fala com o endereço de `JARVIS_PROTHEUS_URL` (o seu ERP, na sua rede ou na TOTVS Cloud) e o que ele lê de lá vai para a API da Anthropic, já mascarado, para ele poder contar e comparar com a especificação.
- A previsão do tempo consulta o Open-Meteo (geocoding-api.open-meteo.com e api.open-meteo.com) com o nome da cidade e as coordenadas dela.
- Na missão, o que o agente lê (arquivos das pastas liberadas, resultados de pesquisa) vai para a API, como na conversa.
- O binário do Claude Code não guarda mais a conversa em disco, nem as fotos. O histórico é só o `.jarvis\registro.db`, que guarda texto.
- A saída de um comando de terminal (até cerca de 12 mil caracteres de cada vez) vai para a API, para o Jarvis poder contar o resultado. Não peça a ele para mostrar pelo terminal arquivos com senha ou chave.
- Em um trabalho de programação, os arquivos do projeto que o trabalhador lê e a saída dos comandos que ele roda vão para a API. As dependências vêm dos registros de pacotes de sempre (npm, PyPI), e uma skill de fora é baixada do repositório que você indicou.

## A pasta `.jarvis`

- `registro.db` — o histórico das conversas.
- `prompt-sistema.md` — o prompt montado na última execução, para conferir o que o modelo recebeu.
- `agente.log` — avisos do binário do Claude Code.
- `jarvis.log` — avisos das bibliotecas de voz e erros internos das ações. Se o Jarvis disser "erro interno", o detalhe está aqui.
- `vozes\` e `whisper\` — os modelos baixados.
- `painel.json` — os ajustes da engrenagem do painel: cor, tema, movimento e voz.
- `rotinas.json` — os lembretes e as tarefas marcados.
- `bom-dia.json` — a data do último cumprimento do dia.
- `missoes.json` e `missoes\` — o registro das missões e, por missão, o passo a passo e o prompt.
- `voz-do-dono.npz` — as assinaturas da sua voz (nenhum áudio); `modelos\` — o modelo de reconhecimento de voz.
- `elevenlabs.json` e `elevenlabs-frases\` — o gasto do mês na voz da nuvem e as frases fixas já sintetizadas.
- `terminal.log` — cada comando de terminal pedido: o texto, a pasta, se você confirmou e como terminou.
- `trabalhos\` — um registro (`.json`) e um passo a passo (`.log`) por trabalho de programação.
- `copias\` — as cópias de trabalho dos projetos, enquanto um trabalho roda.
- `diario\` — o diário de cada projeto: o que cada trabalho fez e deixou pendente. É texto; pode editar.
- `skills\` — as skills de fora que você instalou.

## Onde mexer

- `persona.md` — quem ele é, como fala, quando age e quando usa a memória. Só texto, sem código.
- `jarvis/acoes.py` — o que cada ação valida e faz, a lista de programas e as extensões recusadas.
- `jarvis/janelas.py` — abas, janelas e o que acontece dentro delas: a lógica (`Janelas`), a conversa com o Windows (`SistemaWindows`), as teclas permitidas, as páginas reservadas e os programas que ficam de fora.
- `jarvis/atalho.py` — a tecla de atalho global.
- `jarvis/painel.py` e `jarvis/painel.html` — o painel: o que o Jarvis publica, como a página desenha a rede e os cartões, como o pedido digitado e os ajustes da engrenagem chegam à conversa. A página reproduz o desenho "Jarvis Visual 3D" elemento por elemento; as cores, as formas e quantos neurônios cada coisa vale estão no começo do script dela. Os ajustes de voz chegam à voz de verdade por `cli.py`, `ajustar_voz`.
- `jarvis/projetos.py` — o cadastro dos projetos, os comandos de git que o Jarvis usa e o diário.
- `jarvis/comandos.py` — o que o trabalhador pode rodar: as listas de programas liberados e negados e as regras de cada um.
- `jarvis/trabalhos.py` — a oficina: como um trabalho começa, as regras que o trabalhador recebe, os limites e o fechamento.
- `jarvis/skills.py` — de onde uma skill pode vir e o que é mostrado antes de instalar.
- `jarvis/terminal.py` — como um comando é conferido, chamado no PowerShell, limitado no tempo e registrado.
- `jarvis/rotinas.py` — a agenda de lembretes e tarefas e o despertador; a entrega na conversa está em `cli.py`, `entregar_rotina` e `lembrar`.
- `jarvis/bom_dia.py` e `jarvis/clima.py` — quando cumprimentar e o roteiro do bom dia; a previsão do tempo (Open-Meteo) e o cartão dela.
- `jarvis/missoes.py` — as missões em segundo plano: o prompt do agente, as ferramentas que ele recebe (`PROPRIAS`), os limites e o registro.
- `jarvis/voz/dono.py` — o cadastro e a conferência da voz do dono: o modelo, o limiar, o aprendizado.
- `jarvis/atalhos.py` — o atalho com ícone na Área de Trabalho e no menu Iniciar.
- `jarvis/protheus.py` — a leitura do Protheus: a lista fechada de rotas, o GET fixo, o teto por turno, as travas e o mascaramento.
- `jarvis/voz/nuvem.py` — a voz da nuvem (ElevenLabs): o pedido à API, o crédito do mês, as falhas e a reserva local.
- `jarvis/ferramentas.py` — ferramentas próprias. Ação nova passa por `agir()`, que pede a confirmação.
- `jarvis/guarda.py` — pastas e arquivos protegidos e a regra do turno limpo.
- `jarvis/agente.py` — quais ferramentas existem e o modo de permissão.
- `jarvis/memoria.py` e `jarvis/registro.py` — as pastas de anotações e o banco das conversas.
- `jarvis/cli.py` — o laço de conversa, os comandos e a tela de confirmação. Também as frases com que ele responde ao nome (`RESPOSTAS_AO_NOME`) e as palavras que mandam parar (`PARADAS`).
- `jarvis/voz/` — frases, síntese (Piper) e gravação com transcrição (faster-whisper).
- `jarvis/voz/vigilia.py` — a palavra de ativação: como a fala é separada do silêncio, o que conta como chamado, os tempos (pausa que já vale conferir, silêncio que encerra o pedido, espera depois do nome) e o quanto alguém precisa falar acima da voz dele para ser ouvido.
- `jarvis/segundo_plano.py` e `iniciar.pyw` — o terminal escondido, o ícone e o menu dele, o início com o Windows, a trava de um Jarvis só e a consulta de tela bloqueada.

## Testes

```powershell
pip install pytest
python -m pytest
```

## Licenças

O `piper-tts` é GPL-3.0-or-later; `faster-whisper`, `sounddevice` e `sherpa-onnx` são MIT; `send2trash` é BSD; `pystray` (o ícone) é LGPL-3.0. O modelo de reconhecimento de voz (3D-Speaker ERes2Net, VoxCeleb) é distribuído pelo projeto sherpa-onnx sob Apache-2.0. Para uso pessoal isso não muda nada. Se um dia você distribuir o Jarvis junto com o Piper, confira antes as obrigações da GPL.

As fontes do painel (IBM Plex Sans e IBM Plex Mono) vão embutidas em `jarvis/painel.html`, sob a SIL Open Font License 1.1; o texto da licença está em `jarvis/LICENCA-IBM-Plex.txt`.

## Próximas fases

6. Comandos demorados em segundo plano (servidor de desenvolvimento, build longo), com o Jarvis acompanhando a saída.
7. Rotinas que mexem na máquina com a sua confirmação adiantada (hoje uma tarefa marcada que precisa do `s` espera você).
