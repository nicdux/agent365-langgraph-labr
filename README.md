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

Agente > A temperatura em São Paulo é 21,4 °C.
```

Comandos: `/sair`, `/novo`, `/ajuda` e `/purview`.

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

**Microsoft Defender** → Busca avançada:

```kql
CloudAppEvents
| where Timestamp > ago(2h)
| where ActionType contains "Agent"
| take 20
```

> Requer o **conector Microsoft 365** habilitado em
> Configurações → Segurança para IA, com *Microsoft Entra ID Management
> events* e *Microsoft 365 activities*. Sem ele a tabela fica vazia.

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
