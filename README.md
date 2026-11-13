# Agente de laboratório — Microsoft Agent 365 + LangGraph + Azure OpenAI

Um agente mínimo, escrito em Python com LangGraph, que responde perguntas
sobre o clima e envia telemetria completa para o **Microsoft Agent 365**.

O objetivo não é o agente. O objetivo é provar, com evidência técnica, que um
agente **desenvolvido por você**, rodando fora das plataformas de agente da
Microsoft, recebe identidade no Microsoft Entra, aparece no Agent Registry e
tem sua atividade observada pelo Defender e pelo Purview.

---

## O que você vai construir

```
Você digita no terminal
        ↓
   LangGraph  ──▶  Azure OpenAI (sem chave, via Entra)
        ↓                │
   Ferramenta de clima ──┘
        ↓
   OpenTelemetry
        ↓
   Microsoft Agent 365  ──▶  Admin Center · Defender · Purview
```

Cada mensagem gera um turno de telemetria com três spans: `invoke_agent`
(a invocação), `chat` (a chamada ao modelo) e `execute_tool` (a ferramenta).

## Interface web — Pergunte ao Eduardo

Além do agente original de terminal, o projeto inclui uma interface web com
as abas **O laboratório** e **Pergunte ao Eduardo**, ambas exclusivamente em
dark mode. O chat agora é especializado em **tecnologias Microsoft**.
**Não é o próprio Eduardo e não fala em nome dele.**

**Mudança intencional de escopo:** perguntas biográficas, sobre trajetória
pessoal e outros assuntos gerais deixam de ser respondidas pelo chat web.
O avatar, a navegação, o histórico, os controles de acesso e o agente
original de terminal foram preservados.

O avatar é uma ilustração criada a partir da foto fornecida para esta
experiência. A foto original não é incluída no repositório. Se a ilustração
não carregar, a interface exibe as iniciais **EC**.

A interface continua usando HTML, CSS e módulos JavaScript nativos, sem
troca de framework. Um build pequeno com esbuild empacota **marked**,
**DOMPurify** e **highlight.js** para Markdown sanitizado e código destacado.
Não há CDN, fontes externas ou chamadas Azure feitas pelo navegador.
O backend FastAPI é independente de `agent.py`: não importa nem inicializa o
agente de terminal, seus exportadores de telemetria ou o fluxo de DLP.
**As evidências do Agent 365/Purview descritas neste laboratório continuam
pertencendo ao agente de terminal, não ao chat web.**

### Executar localmente

Com Python 3.12 ou superior, Node.js 20 ou superior e o ambiente virtual ativado:

```powershell
python -m pip install -r requirements.txt
npm ci
npm run build:web
Copy-Item .env.template .env  # somente se voce ainda nao tem um .env
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --no-proxy-headers --no-access-log
```

Abra `http://127.0.0.1:8000`. Não sobrescreva um `.env` já configurado.
O chat web reutiliza `AZURE_OPENAI_ENDPOINT`,
`AZURE_OPENAI_DEPLOYMENT_NAME` e `AZURE_OPENAI_API_VERSION` do deployment
existente; não cria nem publica outro modelo. O deployment precisa suportar
Chat Completions com **structured outputs / JSON Schema**. Não altere o
deployment apenas para iniciar a interface. O bundle gerado não é
versionado: execute `npm run build:web` após alterar o frontend e antes de
empacotar ou publicar a aplicação Python.

No modo local, `AZURE_OPENAI_AUTH_MODE=auto` usa `DefaultAzureCredential`,
como o agente original. Use `az login --tenant SEU-TENANT-ID` e atribua
**Cognitive Services OpenAI User** à identidade utilizada. Não é necessário
configurar o Agent 365 para abrir a interface web.

### Domínio Microsoft e defesa em camadas

**Não existe scraping nem importação automática do LinkedIn.** O endereço
informado é somente um link de referência. Nem o servidor nem o modelo
visitam essa página.

O arquivo [knowledge/eduardo.json](knowledge/eduardo.json) foi mantido para
os metadados visuais do perfil e compatibilidade da estrutura manual.
Seus registros profissionais **não são fonte factual para o novo chat
técnico**. Nenhuma biografia ou opinião foi inferida da foto ou do link.

O fluxo em [backend/assistant.py](backend/assistant.py) usa o **mesmo
deployment** nas três etapas:

1. **Classificação semântica estruturada:** decide `allow`, `refuse`,
   `clarify` ou `injection`. Não é um bloqueio por palavras-chave. O histórico
   permite compreender perguntas de continuação, mas não redefinir o escopo.
2. **Geração restrita:** apenas perguntas permitidas chegam ao modelo
   principal, com as instruções de domínio e comportamento.
3. **Validação independente da saída:** outra chamada estruturada verifica
   domínio, respeito às instruções e cautela factual antes da liberação.
   A validação também se aplica a pedidos de esclarecimento.

As instruções e schemas ficam em
[backend/guardrails.py](backend/guardrails.py). Perguntas e histórico são
serializados como **dados na mensagem de usuário**, nunca interpolados nas
instruções de sistema. Papéis falsos, documentos e supostas autorizações
anteriores não ganham privilégio.

Integrações, migrações e comparações com terceiros são permitidas quando
há relação concreta com Microsoft. Apenas inserir a palavra "Microsoft"
em uma receita, trivia ou pedido de entretenimento não autoriza a resposta.
Solicitações ambíguas recebem uma pergunta de esclarecimento conservadora.

Para perguntas claramente fora do domínio, a resposta é exatamente:

> Sou um assistente especializado em tecnologias Microsoft. Posso ajudar com dúvidas sobre Azure, Microsoft 365, Security, Copilot, Entra, Defender e outras soluções Microsoft.

### Referências, precisão e limitações

O catálogo em [backend/guardrails.py](backend/guardrails.py) contém URLs
Microsoft Learn verificadas e resumos curtos. O modelo escolhe somente IDs
desse catálogo; o servidor monta as referências. Links inventados na prosa
impedem a liberação da resposta. Endpoints técnicos em código permanecem
permitidos. No navegador, links de Markdown também se limitam às referências
HTTPS retornadas pelo servidor.

**Não há busca web ou RAG em tempo real.** Referências são documentação
relacionada, não uma alegação de leitura integral dessas páginas. O campo
`grounded` permanece `false` por compatibilidade: o chat não declara
fundamentação documental completa. Mudanças recentes, preços, licenciamento
e status preview/GA exigem consulta e confirmação na documentação atual.

Os validadores semânticos são probabilísticos e usam o mesmo deployment:
podem errar e compartilhar limitações com o gerador. System prompts e
schemas **não garantem ausência absoluta de jailbreaks ou alucinações**.
Há defesa em camadas, recusa conservadora, testes e controles nativos, mas
casos de alto impacto exigem revisão humana e fontes oficiais atualizadas.

### Controles da aplicação versus serviço Azure

| Camada | Controle |
|---|---|
| Aplicação | Instruções Microsoft, classificação semântica, validação posterior e esclarecimento |
| Aplicação | JSON Schema estrito, referências permitidas, limites, timeout e erros explícitos |
| Aplicação | Markdown sanitizado com DOMPurify, sem imagens/HTML ativo, links restritos e CSP |
| Azure | Política de content filtering associada ao deployment, incluindo categorias e severidades |
| Azure | Prompt Shields/jailbreak e proteção contra ataques em documentos, conforme a política disponível e configurada |

A aplicação **não cria deployments nem altera/desabilita políticas Azure**.
Ela respeita `content_filter` em HTTP 400, `finish_reason=content_filter`
e recusas do modelo, sem nova tentativa para contornar o bloqueio.
Um bloqueio nativo não é tratado como falha de disponibilidade.

Verifique a política associada (`raiPolicyName`) no deployment e os controles
de Content Safety/Prompt Shields no portal Microsoft Foundry. A presença
de um campo de diagnóstico não prova que todos os recursos opcionais estejam
habilitados. Não reduza filtros para fazer testes adversariais passarem.

Eventos `assistant_guardrail` no logger `backend.assistant` (nível INFO)
registram apenas resultados enumerados: classificação, bloqueio da saída,
incerteza, content filtering e erros. Contadores são locais ao processo,
sem prompts, respostas ou IDs de usuário. Integre esse logger ao monitoramento
do host; ele não substitui os exportadores Agent 365 do agente de terminal.

### Segurança, privacidade e limites

- O navegador chama apenas `/api/session` e `/api/chat`, na mesma origem.
  Endpoint, deployment, chaves e tokens do Azure permanecem no servidor.
- O modo padrão `EDUARDO_AUTH_MODE=local` aceita somente clientes de loopback
  e hosts `localhost`/`127.0.0.1`. **Não use túnel, proxy ou bind público
  nesse modo.** O comando acima desabilita confiança em headers de proxy.
- A API valida origem e token CSRF com cookie HttpOnly/SameSite Strict.
  Não habilita CORS. Há CSP sem scripts inline, proteção contra frames,
  validação de host e respostas da API sem cache.
- Não são registrados prompts, respostas, identidades ou conteúdo da base.
  Logs contêm apenas resultados/categorias/códigos. Não habilite debug de
  bibliotecas HTTP/OpenAI em ambientes com dados reais.
- O chat web não envia conteúdo aos exportadores do Agent 365. Perguntas,
  histórico limitado e referências de domínio são processados pelo Azure OpenAI;
  confira também a política de tratamento de dados do seu recurso Azure.
- O histórico fica em `sessionStorage` **apenas nesta aba do navegador**,
  limitado a 20 pares de pergunta/resposta, inclusive após atualizar a
  página. **Nova conversa** cancela a espera e remove o histórico salvo.
  Nada é persistido pelo servidor.
- Perguntas são texto; respostas usam Markdown sanitizado, sem HTML ativo.
  Há botão de copiar, destaque de sintaxe e rolagem em tabelas/código. Há labels,
  navegação de abas por setas/Home/End, envio por Enter, nova linha com
  Shift+Enter, anúncios acessíveis de carregamento/erro e redução de movimento.

| Limite | Valor |
|---|---|
| Pergunta | 2.000 caracteres |
| Histórico enviado ao modelo | Até 10 mensagens, em pares, e 12.000 caracteres |
| Corpo HTTP | 64 KiB |
| Perfil manual preservado | 128 KiB; não usado como fonte técnica |
| Resposta | Até 6.000 caracteres e 3 referências oficiais |
| Tokens por etapa | Classificação 1.024; geração 2.048; validação 1.024 |
| Timeout total do fluxo | Padrão 30 s; configurável entre 1 e 60 s |
| Frequência | 20 perguntas/minuto por usuário (ou loopback local) |
| Concorrência | Até 4 turnos simultâneos por processo |

O limitador é em memória e por processo. Antes de escalar para várias
instâncias/workers, adicione limitação compartilhada no gateway e mantenha
limites de custo e quotas no Azure OpenAI. Uma resposta aceita usa três
chamadas ao mesmo deployment; recusas iniciais usam uma. Quotas do provedor
podem ser menores que o limite local. HTTP 429 do provedor é apresentado
explicitamente com orientação de espera, nunca como recusa de domínio.

### Hospedagem no Azure

A aplicação não foi provisionada ou implantada automaticamente. Para
hospedá-la em **Azure App Service**:

1. Habilite HTTPS e **Authentication / Microsoft Entra ID (Easy Auth)**,
   exija autenticação em todas as rotas e restrinja o tenant e os usuários
   autorizados. Bloqueie acesso direto ao processo que contorne o Easy Auth.
2. Configure `EDUARDO_AUTH_MODE=appservice` e
   `EDUARDO_ALLOWED_HOSTS=SEU-APP.azurewebsites.net` (hosts exatos, separados
   por vírgula quando houver domínio próprio). A aplicação verifica o
   ambiente do App Service e exige os headers de identidade inseridos pelo
   Easy Auth; não use esse modo atrás de um proxy genérico.
3. Use `AZURE_OPENAI_AUTH_MODE=auto` ou `managed_identity`, habilite a
   identidade gerenciada do App Service e conceda a ela o papel
   **Cognitive Services OpenAI User** no recurso. Para identidade atribuída
   pelo usuário, configure `AZURE_MANAGED_IDENTITY_CLIENT_ID`.
4. Configure as três variáveis existentes do Azure OpenAI nas application
   settings. Não coloque configurações do servidor em arquivos do frontend.
5. Execute `npm ci` e `npm run build:web` antes de empacotar a aplicação.
6. Se o uso de chave for indispensável, configure
   `AZURE_OPENAI_AUTH_MODE=api_key` e `AZURE_OPENAI_API_KEY` **por referência
   ao Azure Key Vault nas application settings**, com acesso da identidade
   gerenciada ao segredo. Nunca inclua a chave no código ou no Git.

O modo App Service usa cookie Secure com prefixo `__Host-`, valida origem
HTTPS e exige identidade Entra (`aad`). O modo local recusa iniciar quando
detecta que está hospedado no App Service. A confiança nos headers
`X-MS-CLIENT-PRINCIPAL-*` só é válida na fronteira protegida do Easy Auth.

### Validar sem credenciais ou dados reais

```powershell
python -m unittest discover -s tests -p test_eduardo.py -v
npm run test:web
npm run build:web
npx playwright install chromium --only-shell
npm run test:browser
```

Os testes locais usam conteúdo sintético e chamadas Azure simuladas: validam
a orquestração, e **não medem sozinhos a qualidade semântica do modelo**.
Cobrem domínio, integrações, ambiguidades, injection, histórico adulterado,
saída fora do domínio, referências inventadas, filtros nativos, quotas,
timeouts, contratos, CSRF, origem, autenticação e arquivos estáticos.
Os testes Node validam limites de histórico, Unicode, links e contratos.
Os testes Playwright são reproduzíveis e não chamam Azure: usam um servidor
local separado, mocks explícitos de API e verificam as duas abas em cinco
larguras, contraste mínimo 4,5:1, foco/hover, teclado, loading, Markdown,
sanitização, cópia, persistência, cancelamento e erros.

Há também uma suíte real opt-in em
[tests/test_azure_live.py](tests/test_azure_live.py), com 13 cenários, usando
somente perguntas sintéticas e o deployment configurado:

```powershell
$env:RUN_AZURE_LIVE_TESTS = "true"
python -m unittest discover -s tests -p test_azure_live.py -v
Remove-Item Env:\RUN_AZURE_LIVE_TESTS
```

Ela cobre o fluxo completo, integrações, perguntas gerais, ataques diretos
e codificados, histórico adulterado, follow-ups e validação independente da
saída. Content filtering nativo é aceito como bloqueio de ataques, nunca
desabilitado para fazer o teste passar. A suíte fica desativada por padrão
porque **consome quota Azure**. Não use dados reais ou segredos nos casos.
Somente a suíte real pode aguardar e repetir uma vez após HTTP 429,
respeitando `Retry-After` até 120 s. Não há retentativa para content filtering;
a API interativa continua apresentando a quota explicitamente ao usuário.

Na validação visual, confira as duas abas em desktop/mobile, o esquema dark,
contraste, teclado, foco/hover, carregamento, cópia, Markdown/código, erros,
persistência e limpeza. A preferência de sistema por light mode não muda
o tema. `prefers-reduced-motion` desabilita animações e rolagem suave.

---

## Pré-requisitos

| Item | Detalhe |
|---|---|
| Assinatura Azure | Com permissão para criar recursos |
| Tenant Microsoft Entra | Com **Agent 365** ou **Microsoft 365 E7** |
| Licença | **Atribuída a um usuário** — não basta existir no tenant |
| Função | Global Administrator |
| Estação | Windows com PowerShell |
| Python | 3.12 ou superior |
| .NET | 8.0 ou superior |

> **Atenção à licença.** A telemetria só é aceita se pelo menos um usuário do
> tenant tiver Agent 365 ou E7 **atribuído**. Sem isso o agente funciona
> normalmente, o HTTP responde 200 e a telemetria é descartada em silêncio.

---

## Etapa 1 — Ferramentas

No PowerShell:

```powershell
dotnet --version
dotnet tool install --global Microsoft.Agents.A365.DevTools.Cli
winget install --exact --id Microsoft.AzureCLI
```

Feche e reabra o terminal para o PATH recarregar. Depois:

```powershell
a365 -h
az --version
```

> **Se `a365 -h` reclamar que o framework 8.0 não foi encontrado**, você tem
> apenas um .NET mais novo instalado. Corrija com:
> ```powershell
> [System.Environment]::SetEnvironmentVariable("DOTNET_ROLL_FORWARD","Major","User")
> ```
> Reabra o terminal e tente de novo.

Faça login no tenant do laboratório:

```powershell
az login --tenant SEU-TENANT-ID
```

---

## Etapa 2 — Projeto

```powershell
git clone SEU-REPOSITORIO
cd Weatherapp-Agent365SDK
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

O prompt deve passar a exibir `(.venv)`.

> Se o `Activate.ps1` for bloqueado:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

---

## Etapa 3 — Azure OpenAI

```powershell
az group create --name rg-a365-lab --location eastus

az cognitiveservices account create --name SEU-NOME-UNICO --resource-group rg-a365-lab --location eastus --kind OpenAI --sku S0 --custom-domain SEU-NOME-UNICO --yes
```

> **O `--custom-domain` não é opcional.** Sem ele o endpoint fica no formato
> regional e a autenticação por token do Entra não funciona. Com ele você
> obtém `https://SEU-NOME-UNICO.openai.azure.com/`, que é o formato correto.

Desabilite a autenticação por chave:

```powershell
az resource update --resource-group rg-a365-lab --name SEU-NOME-UNICO --resource-type "Microsoft.CognitiveServices/accounts" --set properties.disableLocalAuth=true
```

Veja os modelos disponíveis e crie o deployment:

```powershell
az cognitiveservices account list-models --name SEU-NOME-UNICO --resource-group rg-a365-lab --query "[?contains(name,'gpt')].{Modelo:name, Versao:version}" -o table

az cognitiveservices account deployment create --name SEU-NOME-UNICO --resource-group rg-a365-lab --deployment-name gpt-5.4-mini --model-name gpt-5.4-mini --model-version 2026-03-17 --model-format OpenAI --sku-name GlobalStandard --sku-capacity 10
```

> **Modelos da família gpt-5 exigem `GlobalStandard`**, não `Standard`.
> Se o SKU for recusado, liste os aceitos pelo modelo antes de tentar outro.

Dê a si mesmo permissão de acesso sem chave:

```powershell
az role assignment create --role "Cognitive Services OpenAI User" --assignee SEU-EMAIL --scope /subscriptions/SUA-SUB/resourceGroups/rg-a365-lab/providers/Microsoft.CognitiveServices/accounts/SEU-NOME-UNICO
```

---

## Etapa 4 — Arquivo .env

```powershell
Copy-Item .env.template .env
```

Abra o `.env` e preencha as três primeiras variáveis com os dados do seu
recurso do Azure OpenAI. As variáveis do Agent 365 serão preenchidas
automaticamente na próxima etapa.

O bloco `PURVIEW_*` no fim do arquivo é opcional — deixe como está por
enquanto. Ele só entra em jogo se você quiser o bloqueio em tempo real, na
seção *Purview SDK — o app cliente*.

---

## Etapa 5 — Blueprint e identidade

```powershell
a365 setup requirements
a365 setup all --agent-name "MeuAgente" --authmode s2s
```

O `--authmode s2s` é obrigatório: ele concede a permissão de aplicativo
`Agent365.Observability.OtelWrite`, sem a qual a telemetria é recusada.

Durante a execução:

- Responda **`y`** quando perguntar sobre a permissão de observabilidade
- Complete o consentimento no navegador quando ele abrir

> **O terminal vai imprimir o client secret em texto claro.** Se estiver com a
> tela compartilhada, interrompa antes. Nunca cole o log em chamados ou chats.

Ao final o CLI grava as variáveis `AGENT365OBSERVABILITY__*` no seu `.env`.

---

## Etapa 6 — Conversar com o agente

```powershell
python agent.py
```

```
====================================================================
  Microsoft Agent 365 - Agente de laboratorio
====================================================================
  Agente   : MeuAgente Identity
  Modelo   : gpt-5.4-mini
  Telemetria: Microsoft Agent 365 (app-only, S2S)
====================================================================

Voce > Qual a temperatura em Sao Paulo?

Agente > A temperatura em São Paulo é 21,4 °C. (1.3s)
```

Comandos: `/sair`, `/novo`, `/limpar`, `/ajuda` e `/purview`.

O terminal usa cores ANSI (desativadas automaticamente se a saída não for
um console interativo, ou definindo a variável `NO_COLOR`) e mostra o tempo
de resposta de cada turno.

O `/purview` envia um prompt com **registros fictícios** contendo dados que
parecem sensíveis — números de documento, telefones e endereços. Serve para
acionar a detecção de dados sensíveis do Purview e demonstrar a governança.
Nenhum dado real de pessoa está no repositório.

Para ver os spans em JSON enquanto conversa, coloque `MOSTRAR_SPANS=true`
no `.env`.

---

## Etapa 7 — Ver as evidências

**Microsoft 365 Admin Center** → Agentes → Todos os agentes. Seu agente
aparece no registro com identidade Entra e proteções do Purview aplicadas.
Pode levar cerca de uma hora após o registro.

**Microsoft Defender** → Busca avançada. Para ver as chamadas do agente ao
Purview, use `GraphAPIAuditEvents` — trocando o AppId pelo seu
`PURVIEW_CLIENT_APP_ID`:

```kql
GraphAPIAuditEvents
| where Timestamp > ago(24h)
| where ApplicationId == "<APP-ID-DO-CLIENTE-PURVIEW>"
| project Timestamp, RequestMethod, ResponseStatusCode, RequestUri
| order by Timestamp desc
```

> Não use `CloudAppEvents` para isso: ela exige licença de **Microsoft Defender
> for Cloud Apps** e, sem ela, fica permanentemente vazia — a consulta não dá
> erro, apenas retorna "No results found". O [EVIDENCIAS.md](EVIDENCIAS.md)
> detalha como distinguir os dois casos.

> Se o navegador estiver traduzindo a página, desligue a tradução para
> `security.microsoft.com`. O Chrome traduz as palavras-chave do KQL e
> quebra a consulta.

**Microsoft Purview** → DSPM → AI observability. Use a versão atual, não a
clássica: a clássica não suporta Agent 365.

---

## Atribuição no Defender

Preencha estas três variáveis no `.env` com os dados de quem vai usar o agente:

```
A365_CALLER_USER_ID=<object id do usuario no Entra>
A365_CALLER_USER_EMAIL=<email>
A365_CALLER_USER_NAME=<nome>
```

São elas que respondem *"quem pediu"* no Defender. Sem elas, a atividade é
registrada sem identificar o solicitante, e o agente avisa na abertura.

As variáveis `A365_AGENT_AUID` e `A365_AGENT_EMAIL` descrevem a **conta de
usuário do próprio agente**, que só existe no caminho AI Teammate — este
requer o programa Frontier. Num Standard Agent, deixe as duas em branco.

---

## Telemetria elegivel para o Microsoft Defender

Emitir spans nao basta. Para que a atividade apareca como **Activity** no
Defender e no centro de administracao, o turno precisa carregar um conjunto
especifico de atributos e operacoes:

| Item | Por que e necessario |
|---|---|
| `microsoft.channel.name` | Exigido para o registro ser classificado como Activity |
| `microsoft.session.id` | Agrupa os turnos de uma mesma sessao |
| Operacao `output_messages` | A saida precisa de escopo proprio, nao so um atributo no span raiz |
| `record_response()` na ferramenta | O resultado da ferramenta precisa ser registrado explicitamente |

O `agent.py` deste repositorio ja implementa os quatro. Um helper unico
constroi todos os `Request` com canal, sessao e conversa, e um `ContextVar`
propaga o contexto ate as ferramentas sem estado global mutavel.

Sem esses itens a exportacao retorna HTTP 200, os spans existem e a telemetria
e aceita, mas nada aparece no Defender. E uma falha silenciosa.

### Como verificar

Instale as Agent 365 Skills e peca ao assistente de codigo:

```
validate this Agent 365 integration
```

O `a365-code-validator` executa um preflight sobre o projeto e aponta os
atributos e operacoes ausentes. Foi ele que identificou os quatro itens acima
neste laboratorio.

---

## Purview SDK — o app cliente

Esta seção e a próxima são **opcionais**. Elas só são necessárias se você quiser
que o agente **bloqueie** prompts e respostas em tempo real. Sem elas o agente
funciona por completo — identidade, telemetria, Defender e classificação no
DSPM. O que falta é apenas a decisão em runtime.

O `agent.py` chama dois endpoints do Microsoft Graph a cada turno:
`protectionScopes/compute`, que descobre as políticas aplicáveis, e
`processContent`, que envia o conteúdo e recebe a ação a executar. Para isso ele
precisa de um app registrado no Entra — e **esse app não é criado pelo
`a365 setup all`**. Você o cria à mão, uma vez.

### Criar o app

Em **Entra ID → App registrations → New registration**, dê um nome (por
exemplo `A365 Lab Purview Client`) e mantenha *Single tenant*. Não é preciso
redirect URI.

Em **API permissions → Add a permission → Microsoft Graph → Application
permissions** (não *Delegated*), adicione:

| Permissão | Para quê |
|---|---|
| `Content.Process.All` | Enviar conteúdo ao `processContent` |
| `ProtectionScopes.Compute.All` | Descobrir as políticas aplicáveis |

Depois clique em **Grant admin consent**. As duas precisam ficar com o sinal
verde — sem consent, a chamada falha em runtime.

Em **Certificates & secrets → New client secret**, copie o valor na hora: ele
não é exibido de novo.

### Preencher o `.env`

```
PURVIEW_ENABLED=true
PURVIEW_CLIENT_APP_ID=<Application (client) ID do app que você acabou de criar>
PURVIEW_CLIENT_SECRET=<o segredo>
PURVIEW_USER_ID=<object id do usuário que interage com o agente>
PURVIEW_FAIL_CLOSED=false
```

O segredo precisa ficar em `PURVIEW_CLIENT_SECRET`. Se você usar o nome
`AZURE_CLIENT_SECRET`, o `DefaultAzureCredential` adota este app para **tudo**,
inclusive o Azure OpenAI, e o modelo passa a responder `401 PermissionDenied`
sem nenhuma pista da causa real.

Com o app pronto, siga para a política de DLP abaixo — é ela que dá ao Purview
algo a decidir.

---

## DLP do Purview — do audit ao bloqueio

Até aqui o Purview **observa**: classifica o conteúdo e mostra a atividade no
DSPM. Com uma política de DLP, ele passa a **decidir**: o `processContent`
devolve `restrictAccess` e o agente recusa a requisição.

### Por que via PowerShell, e não pelo portal

O portal do Purview não permite criar essa política. No assistente de DLP, a
location **Managed cloud apps** aparece sem caixa de seleção, bloqueada por dois
pré-requisitos que o assistente exige para políticas de app gerenciado:
*Conditional Access policy not found* e *Edge for business protection settings
not found*.

O caminho por PowerShell não depende de nenhum dos dois. Ele usa o enforcement
plane `Application` — que substituiu o antigo `Entra` — e é o único cenário
documentado pela Microsoft para apps registrados no Entra.

### O ponto que faz a política não funcionar

A política precisa escopar o **AppId que o código declara** em
`protectedAppMetadata.applicationLocation` e no `protectionScopes/compute` —
no `agent.py`, o `PURVIEW_CLIENT_APP_ID`.

Escopar apenas o blueprint do agente **não funciona**: o Purview não encontra
política aplicável, devolve "sem restrição" e o conteúdo passa. Sem erro, sem
aviso, sem nada no log. É a mesma classe de falha silenciosa da telemetria.

Na dúvida, coloque os dois no escopo — o app cliente do Purview e o blueprint.

### Rodar

```powershell
Install-Module ExchangeOnlineManagement -Scope CurrentUser
.\Create-DlpPolicyForAgent365Lab.ps1
```

Ajuste antes o bloco `$Applications` com os AppIds do seu tenant e o
`$NotifyRecipients` com um e-mail real. Requer a função **Compliance
Administrator** (ou Compliance Data Administrator).

Para testar, rode o agente e use `/purview`. A resposta esperada é:

```
Agente > Sua mensagem foi bloqueada por uma politica do Microsoft Purview.
```

Se o agente responder normalmente, reinicie-o: o ETag do
`protectionScopes/compute` fica em cache por uma hora, e uma sessão antiga
continua usando o escopo de quando a política ainda não existia.

### Fail-open ou fail-closed

A variável `PURVIEW_FAIL_CLOSED` do `.env` decide o que acontece quando a
chamada ao Purview **falha** — timeout, token expirado, HTTP 400.

Com `false`, o agente imprime o erro no console e **responde normalmente**: o
conteúdo segue sem avaliação de política. Com `true`, o agente recusa a
requisição.

**Em produção, use `true`.** Com fail-open, qualquer erro na API vira bypass de
política — e a falha é discreta, uma linha no console enquanto a conversa
continua. Uma requisição malformada ou uma indisponibilidade momentânea bastam.

No teste de bloqueio, porém, `false` é o que dá valor à evidência: se o agente
bloqueou com fail-open ativo, o bloqueio veio de decisão de política e não de
um erro mascarado.

### Erros do `New-DlpCompliancePolicy` neste cenário

| Erro | Causa | Solução |
|---|---|---|
| `Locations input JSON is invalid ... requires a JSON array` | Com um único app, o PowerShell desembrulha o array e gera `{}` em vez de `[{}]` | `ConvertTo-Json -AsArray` |
| `'StopPolicyProcessing' is supported only for Exchange, Teams` | Parâmetro não vale para a location `Applications` | Remova o parâmetro |
| `'SiteAdmin' ... should be valid SMTP address` | `NotifyUser` não aceita a palavra-chave | Use um e-mail real; `SiteAdmin` só vale em `GenerateIncidentReport` |
| `RestrictAccess actions 'DownloadText' are not supported` | `Applications` aceita `UploadText` de forma confiável | Restrinja apenas `UploadText` |
| `The Compliance Policy ... has been deleted and cannot be modified` | A exclusão é assíncrona e o `Get-` ainda devolve o objeto | Crie com um nome novo em vez de esperar |
| Política criada, mas nada é bloqueado | Escopo com o AppId errado | Escope o `PURVIEW_CLIENT_APP_ID` |

Um detalhe do script: sem `-ErrorAction Stop` explícito, um erro de binding de
parâmetro não interrompe a execução, e o script imprime "criada com sucesso"
sobre uma política que nunca existiu.

### A evidência

O evento de `DLPRuleMatch` fica em **Purview → Data Loss Prevention → Alerts**.
Vale abrir a atividade em detalhe: o registro correlaciona, num único evento, a
identidade do agente (`AgentId`, `AgentName`), o usuário em nome de quem ele
operou (`UserId`), o app (`AppIdentity`, na categoria `ConnectedAIApp.Entra`), o
plano de enforcement (`EnforcementPlane: Application`), as ações executadas
— incluindo `RestrictAccess` — e os valores detectados por tipo de informação
sensível, com contagem e confiança.

É esse registro que sustenta a afirmação de que o bloqueio foi decisão de
política, e não comportamento do código.

O [EVIDENCIAS.md](EVIDENCIAS.md) lista, camada por camada, onde verificar cada
evidência no seu tenant e quanto tempo cada uma leva para aparecer.

---

## Como a autenticação funciona


Esta é a parte que costuma travar quem tenta reproduzir, então vale entender.

O agente precisa de um token **app-only**, emitido para a **identidade do
agente**. Mas a identidade não guarda credenciais — quem guarda é o blueprint.
A ponte entre os dois é o parâmetro `fmi_path`, numa cadeia de dois passos:

1. O **blueprint** pede um token com `scope=api://AzureADTokenExchange/.default`
   e `fmi_path=<id-da-identidade>`, declarando por qual identidade está agindo.
2. Esse token vira `client_assertion` numa segunda chamada, que devolve o
   token final, já emitido para a identidade.

O `ClientSecretCredential` do `azure-identity` **não** expõe `fmi_path`, por
isso o `agent.py` chama o endpoint de token diretamente.

---

## Erros comuns

| Erro | Causa | Solução |
|---|---|---|
| `AADSTS1002012` | Escopo delegado em fluxo app-only | Use o sufixo `/.default` |
| `AADSTS82001` | Blueprint agêntico usando client credentials comum | Use a cadeia `fmi_path` |
| `AADSTS7000215` | Secret do blueprint com client_id da identidade | Use a cadeia `fmi_path` |
| `AADSTS65002` | Tentando obter o token pelo Azure CLI | O Azure CLI não é pré-autorizado nesse recurso |
| `HTTP 403` na exportação | `agentId` do token diferente do da URL | Use o token app-only da identidade |
| `HTTP 401/403` logo após o setup | Propagação de permissão | Aguarde alguns minutos |
| Telemetria some sem erro | Licença não atribuída a usuário | Atribua Agent 365 ou E7 |
| Nada acontece ao rodar | Arquivo corrompido ao copiar e colar | Clone o repositório |
| `ModuleNotFoundError` | Ambiente virtual não ativado | `.\.venv\Scripts\Activate.ps1` |
| `Invalid type ToolType` | Enum passado onde se espera string | Já corrigido no `agent.py` |
| Telemetria aceita mas ausente no Defender | Falta `microsoft.channel.name` ou a operação `output_messages` | Rode o `a365-code-validator` |
| `PermissionDenied` no Azure OpenAI | Variáveis `AZURE_CLIENT_*` no `.env` fazem o `DefaultAzureCredential` adotar o app errado | Use nomes próprios, como `PURVIEW_CLIENT_SECRET` |
| `execute_tool` duplicado | Escopo manual + auto-instrumentação do LangChain | Conhecido no SDK 1.0.0; o span manual é o elegível |
| Aviso de depreciação do LangGraph | `create_react_agent` migrou para `langchain.agents` | Suprimido; a função é válida até a v2.0 |

---

## Segurança

- O `.gitignore` já exclui `.env`, `a365.config.json` e `a365.generated.config.json`
- **Nunca** faça commit do client secret
- Em produção, prefira credencial federada ou certificado ao invés de secret
- Use um tenant e um resource group dedicados ao laboratório

## Remover tudo

```powershell
a365 cleanup
az group delete --name rg-a365-lab
```

---

## Arquivos

| Arquivo | Função |
|---|---|
| `agent.py` | O agente e o chat interativo |
| `requirements.txt` | Dependências Python |
| `.env.template` | Modelo de configuração |
| `.gitignore` | Protege segredos |
| `Create-DlpPolicyForAgent365Lab.ps1` | Cria a política de DLP do Purview escopada ao agente |
| `EVIDENCIAS.md` | Onde verificar cada evidência no seu tenant |
| `backend/` | API web, autenticação e orquestração do Azure OpenAI |
| `backend/guardrails.py` | Instruções, schemas e catálogo de referências Microsoft |
| `frontend/` | Design system dark, avatar, Markdown seguro e chat acessível |
| `knowledge/eduardo.json` | Perfil manual preservado; não é fonte técnica do chat |
| `package.json` / `build-web.mjs` | Dependências e build pequeno do frontend |
| `tests/test_eduardo.py` | Testes de guardrails, contratos, API e autenticação |
| `tests/frontend.test.mjs` | Testes de histórico, Unicode e links |
| `tests/browser.spec.mjs` | Regressões de dark mode, acessibilidade e chat |
| `tests/test_azure_live.py` | Validação real opt-in no deployment existente |
