"""
Agente de laboratorio Microsoft Agent 365 - LangGraph + Azure OpenAI.

Chat interativo no terminal. Cada mensagem que voce envia gera um turno
completo de telemetria (invoke_agent -> chat -> execute_tool) exportado
para o Microsoft Agent 365.

Uso:
    python agent.py

Comandos dentro do chat:
    /sair     encerra
    /ajuda    mostra os comandos
    /novo     comeca uma conversa nova
"""
import os
import sys
import threading
import time
import unicodedata
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

import warnings

import requests
from dotenv import load_dotenv

# O LangGraph 1.x marca create_react_agent como depreciado em favor de
# create_agent, com remocao prevista para a 2.0. A funcao continua correta;
# o aviso apenas poluiria a saida do chat.
warnings.filterwarnings("ignore", category=DeprecationWarning, module="langgraph.*")

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from microsoft_agents_a365.observability.core import (
    configure,
    Agent365ExporterOptions,
    AgentDetails,
    InvokeAgentScope,
    InvokeAgentScopeDetails,
    Channel,
    OutputScope,
    Request,
    Response,
    ServiceEndpoint,
    ExecuteToolScope,
    ToolCallDetails,
    ToolType,
    CallerDetails,
    UserDetails,
)
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_openai import AzureChatOpenAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent


# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------
load_dotenv()

OBRIGATORIAS = [
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT_NAME",
    "AZURE_OPENAI_API_VERSION",
    "AGENT365OBSERVABILITY__AGENTNAME",
    "AGENT365OBSERVABILITY__AGENTID",
    "AGENT365OBSERVABILITY__AGENTBLUEPRINTID",
    "AGENT365OBSERVABILITY__TENANTID",
    "AGENT365OBSERVABILITY__CLIENTSECRET",
]

faltando = [v for v in OBRIGATORIAS if not os.environ.get(v)]
if faltando:
    print("ERRO: variaveis ausentes no arquivo .env:")
    for v in faltando:
        print("  -", v)
    print()
    print("Copie o .env.template para .env e rode 'a365 setup all' para")
    print("preencher os valores do Agent 365. Veja o README.")
    sys.exit(1)

AGENT_NAME = os.environ["AGENT365OBSERVABILITY__AGENTNAME"].strip('"')
AGENT_ID = os.environ["AGENT365OBSERVABILITY__AGENTID"]
BLUEPRINT_ID = os.environ["AGENT365OBSERVABILITY__AGENTBLUEPRINTID"]
TENANT_ID = os.environ["AGENT365OBSERVABILITY__TENANTID"]
BLUEPRINT_SECRET = os.environ["AGENT365OBSERVABILITY__CLIENTSECRET"]

# Atribuicao no Defender. Sem estes campos o Defender registra que o agente
# executou, mas nao consegue dizer quem pediu. Preencha no .env.
AGENT_AUID = os.environ.get("A365_AGENT_AUID", "")
AGENT_EMAIL = os.environ.get("A365_AGENT_EMAIL", "")
CALLER_USER_ID = os.environ.get("A365_CALLER_USER_ID", "")
CALLER_USER_EMAIL = os.environ.get("A365_CALLER_USER_EMAIL", "")
CALLER_USER_NAME = os.environ.get("A365_CALLER_USER_NAME", "")

CANAL_TERMINAL = Channel(name="terminal")
_conversa_atual = ContextVar("a365_conversation_id", default=None)


def _criar_request(conteudo, conversa=None):
    conversa = conversa or _conversa_atual.get()
    return Request(
        content=conteudo,
        session_id=conversa,
        channel=CANAL_TERMINAL,
        conversation_id=conversa,
    )


def _validar_atribuicao():
    """Avisa sobre campos vazios que deixam a atribuicao incompleta."""
    # Nao valida A365_AGENT_AUID nem A365_AGENT_EMAIL: eles descrevem a conta
    # de usuario do proprio agente, que so existe no caminho AI Teammate.
    # Em um Standard Agent ficam vazios por definicao.
    avisos = []
    for rotulo, valor, deve_ser_email in (
        ("A365_CALLER_USER_ID", CALLER_USER_ID, False),
        ("A365_CALLER_USER_EMAIL", CALLER_USER_EMAIL, True),
        ("A365_CALLER_USER_NAME", CALLER_USER_NAME, False),
    ):
        if not valor:
            avisos.append(rotulo + " vazio")
        elif deve_ser_email and "@" not in valor:
            avisos.append(rotulo + " nao parece um e-mail")
    return avisos

# Cidades conhecidas pela ferramenta de clima.
CIDADES = {
    "sao paulo": (-23.55, -46.63),
    "rio de janeiro": (-22.91, -43.17),
    "brasilia": (-15.79, -47.88),
    "belo horizonte": (-19.92, -43.94),
    "porto alegre": (-30.03, -51.23),
    "recife": (-8.05, -34.88),
    "salvador": (-12.97, -38.50),
    "curitiba": (-25.43, -49.27),
    "manaus": (-3.12, -60.02),
    "fortaleza": (-3.73, -38.52),
    "goiania": (-16.68, -49.25),
    "belem": (-1.46, -48.50),
    "vitoria": (-20.32, -40.34),
    "florianopolis": (-27.59, -48.55),
    "campinas": (-22.91, -47.06),
    "lisboa": (38.72, -9.14),
    "porto": (41.15, -8.61),
    "madri": (40.42, -3.70),
    "londres": (51.51, -0.13),
    "paris": (48.86, 2.35),
    "nova york": (40.71, -74.01),
    "toquio": (35.68, 139.69),
    "seattle": (47.61, -122.33),
}


def _normalizar(texto):
    """Remove acentos e espacos extras para casar o nome da cidade."""
    sem_acento = unicodedata.normalize("NFD", texto.strip().lower())
    sem_acento = "".join(c for c in sem_acento if unicodedata.category(c) != "Mn")
    return " ".join(sem_acento.split())


# ---------------------------------------------------------------------------
# OpenTelemetry
# ---------------------------------------------------------------------------
recurso = Resource.create(
    {"service.name": AGENT_NAME, "service.namespace": "a365.lab"}
)
provider = TracerProvider(resource=recurso)

if os.environ.get("MOSTRAR_SPANS", "false").lower() == "true":
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

trace.set_tracer_provider(provider)


# ---------------------------------------------------------------------------
# Token app-only da identidade do agente (cadeia de dois passos, fmi_path)
# ---------------------------------------------------------------------------
# Documentado em:
#   https://learn.microsoft.com/en-us/entra/agent-id/
#       autonomous-agent-authentication-authorization-flow
#
# Passo 1: o BLUEPRINT pede um token informando, no parametro fmi_path, por
#          qual identidade de agente ele esta agindo.
# Passo 2: esse token vira client_assertion e devolve o token final, ja
#          emitido para a IDENTIDADE do agente.
#
# Por que nao usar ClientSecretCredential: ele nao expoe fmi_path, e um
# blueprint agentico nao pode usar client_credentials comum contra o recurso
# de observabilidade (o Entra recusa com AADSTS82001).
OBSERVABILITY_RESOURCE = "9b975845-388f-4429-889e-eab1ef63949c"
OBSERVABILITY_SCOPE = "api://" + OBSERVABILITY_RESOURCE + "/.default"
TOKEN_ENDPOINT = (
    "https://login.microsoftonline.com/" + TENANT_ID + "/oauth2/v2.0/token"
)

# O SDK espera tool_type como string. Passar o objeto do enum faz o
# OpenTelemetry recusar o atributo e o Defender fica sem a classificacao
# da ferramenta. "function" = executada em processo; "mcp" = via servidor MCP.
TIPO_FERRAMENTA_FUNCAO = getattr(ToolType.FUNCTION, "value", "function")

_cache_token = {}


def _post_token(dados, etapa):
    resposta = requests.post(TOKEN_ENDPOINT, data=dados, timeout=30)
    if resposta.status_code != 200:
        raise RuntimeError(
            "Falha no " + etapa + " (HTTP " + str(resposta.status_code) + "): "
            + resposta.text[:500]
        )
    corpo = resposta.json()
    return corpo["access_token"], int(corpo.get("expires_in", 3600))


def _obter_token_identidade():
    token_blueprint, _ = _post_token(
        {
            "client_id": BLUEPRINT_ID,
            "scope": "api://AzureADTokenExchange/.default",
            "grant_type": "client_credentials",
            "client_secret": BLUEPRINT_SECRET,
            "fmi_path": AGENT_ID,
        },
        "passo 1 (token do blueprint com fmi_path)",
    )
    token_identidade, expira_em = _post_token(
        {
            "client_id": AGENT_ID,
            "scope": OBSERVABILITY_SCOPE,
            "grant_type": "client_credentials",
            "client_assertion_type": (
                "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
            ),
            "client_assertion": token_blueprint,
        },
        "passo 2 (troca pelo token da identidade)",
    )
    return token_identidade, time.time() + expira_em


def token_resolver(agent_id, tenant_id):
    chave = str(agent_id) + ":" + str(tenant_id)
    entrada = _cache_token.get(chave)
    if entrada and entrada[1] > time.time() + 120:
        return entrada[0]
    token, expira = _obter_token_identidade()
    _cache_token[chave] = (token, expira)
    return token


configure(
    service_name=AGENT_NAME,
    service_namespace="a365.lab",
    exporter_options=Agent365ExporterOptions(
        use_s2s_endpoint=True,
        token_resolver=token_resolver,
    ),
)

# A auto-instrumentacao precisa ser ativada depois do configure().
from microsoft_agents_a365.observability.extensions.langchain import (  # noqa: E402
    CustomLangChainInstrumentor,
)

CustomLangChainInstrumentor()


# ---------------------------------------------------------------------------
# Microsoft Purview - avaliacao de politica em tempo real
# ---------------------------------------------------------------------------
# Duas chamadas ao Microsoft Graph, documentadas em
#   https://learn.microsoft.com/en-us/purview/developer/use-the-api
#
#   protectionScopes/compute  descobre quais politicas se aplicam ao usuario
#   processContent            envia o conteudo e devolve a acao a executar
#
# Quando a resposta traz restrictAccess/block, o agente PRECISA bloquear.
# Sem estas chamadas o Purview apenas audita; com elas, ele controla.
PURVIEW_ENABLED = os.environ.get("PURVIEW_ENABLED", "false").lower() == "true"
PURVIEW_FAIL_CLOSED = os.environ.get("PURVIEW_FAIL_CLOSED", "true").lower() == "true"
PURVIEW_USER_ID = os.environ.get("PURVIEW_USER_ID", CALLER_USER_ID)
PURVIEW_CLIENT_APP_ID = os.environ.get("PURVIEW_CLIENT_APP_ID", "")
# Segredo proprio, nao AZURE_CLIENT_SECRET. Usar o nome AZURE_* faria o
# DefaultAzureCredential adotar este app para TUDO, inclusive o Azure OpenAI,
# provocando 401 PermissionDenied no modelo.
PURVIEW_CLIENT_SECRET = os.environ.get("PURVIEW_CLIENT_SECRET", "")
PURVIEW_GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
PURVIEW_APP_VERSION = "1.0"
MSG_PROMPT_BLOQUEADO = (
    "Sua mensagem foi bloqueada por uma politica do Microsoft Purview."
)
MSG_RESPOSTA_BLOQUEADA = (
    "A resposta do agente foi bloqueada por uma politica do Microsoft Purview."
)

_purview_lock = threading.Lock()
_purview_scope_cache = {}
_PURVIEW_SCOPE_TTL = 3600


def _purview_token():
    """Token app-only do app cliente registrado no Entra."""
    token, _ = _post_token(
        {
            "client_id": PURVIEW_CLIENT_APP_ID,
            "client_secret": PURVIEW_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        "token do Graph para o Purview",
    )
    return token


def _purview_headers():
    return {
        "Authorization": "Bearer " + _purview_token(),
        "Content-Type": "application/json",
        "Client-Request-Id": str(uuid.uuid4()),
    }


def _purview_compute_scopes(user_id, forcar=False):
    """Descobre as politicas aplicaveis e devolve o ETag do processContent."""
    with _purview_lock:
        cache = _purview_scope_cache.get(user_id)
        if not forcar and cache and time.time() < cache[1]:
            return cache[0]

    resposta = requests.post(
        PURVIEW_GRAPH_ROOT + "/users/" + user_id
        + "/dataSecurityAndGovernance/protectionScopes/compute",
        headers=_purview_headers(),
        json={
            "activities": "uploadText,downloadText",
            "locations": [
                {
                    "@odata.type": "microsoft.graph.policyLocationApplication",
                    "value": PURVIEW_CLIENT_APP_ID,
                }
            ],
        },
        timeout=30,
    )
    resposta.raise_for_status()
    etag = resposta.headers.get("ETag")
    with _purview_lock:
        _purview_scope_cache[user_id] = (etag, time.time() + _PURVIEW_SCOPE_TTL)
    return etag


def _purview_bloqueia(payload):
    for acao in payload.get("policyActions", []):
        if (
            acao.get("action", "").lower() == "restrictaccess"
            and acao.get("restrictionAction", "").lower() == "block"
        ):
            return True
    return False


def _purview_process(conteudo, atividade, conversa, sequencia):
    if not PURVIEW_USER_ID:
        raise RuntimeError("PURVIEW_USER_ID ausente no .env.")

    etag = _purview_compute_scopes(PURVIEW_USER_ID)
    agora = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    corpo = {
        "contentToProcess": {
            "contentEntries": [
                {
                    "@odata.type": "microsoft.graph.processConversationMetadata",
                    "identifier": str(uuid.uuid4()),
                    "content": {
                        "@odata.type": "microsoft.graph.textContent",
                        "data": conteudo,
                    },
                    "agents": [
                        {
                            "@odata.type": "microsoft.graph.aiAgentInfo",
                            "blueprintId": BLUEPRINT_ID,
                            "identifier": AGENT_ID,
                            "name": AGENT_NAME,
                            "version": PURVIEW_APP_VERSION,
                        }
                    ],
                    "name": AGENT_NAME + " " + atividade,
                    "correlationId": conversa,
                    "sequenceNumber": sequencia,
                    "isTruncated": False,
                    "createdDateTime": agora,
                    "modifiedDateTime": agora,
                    "contentCategory": "ai",
                }
            ],
            "activityMetadata": {"activity": atividade},
            "deviceMetadata": {
                "deviceType": "Unmanaged",
                "operatingSystemSpecifications": {
                    "operatingSystemPlatform": os.name,
                    "operatingSystemVersion": os.environ.get("OS", "unknown"),
                },
            },
            "protectedAppMetadata": {
                "name": AGENT_NAME,
                "version": PURVIEW_APP_VERSION,
                "applicationLocation": {
                    "@odata.type": "microsoft.graph.policyLocationApplication",
                    "value": PURVIEW_CLIENT_APP_ID,
                },
            },
            "integratedAppMetadata": {
                "name": AGENT_NAME,
                "version": PURVIEW_APP_VERSION,
            },
        }
    }

    for tentativa in range(2):
        cabecalhos = _purview_headers()
        if etag:
            cabecalhos["If-None-Match"] = etag
        resposta = requests.post(
            PURVIEW_GRAPH_ROOT + "/users/" + PURVIEW_USER_ID
            + "/dataSecurityAndGovernance/processContent",
            headers=cabecalhos,
            json=corpo,
            timeout=30,
        )
        resposta.raise_for_status()
        if resposta.status_code in (202, 204) or not resposta.content:
            return False

        resultado = resposta.json()
        erros = resultado.get("processingErrors", [])
        if erros:
            raise RuntimeError("Purview retornou " + str(len(erros)) + " erro(s).")
        if resultado.get("protectionScopeState") == "modified" and tentativa == 0:
            etag = _purview_compute_scopes(PURVIEW_USER_ID, forcar=True)
            continue
        return _purview_bloqueia(resultado)

    return False


def purview_bloqueia(conteudo, atividade, conversa, sequencia):
    """True quando a politica manda bloquear."""
    if not PURVIEW_ENABLED:
        return False
    try:
        return _purview_process(conteudo, atividade, conversa, sequencia)
    except Exception as erro:
        print("  [purview] falha na avaliacao (" + atividade + "):", erro)
        if PURVIEW_FAIL_CLOSED:
            raise
        return False


# ---------------------------------------------------------------------------
# O agente
# ---------------------------------------------------------------------------
provedor_token_openai = get_bearer_token_provider(
    DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
)

llm = AzureChatOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
    api_version=os.environ["AZURE_OPENAI_API_VERSION"],
    azure_ad_token_provider=provedor_token_openai,
)

detalhes_agente = AgentDetails(
    agent_id=AGENT_ID,
    agent_name=AGENT_NAME,
    agent_description="Agente de laboratorio Agent 365 com LangGraph",
    agentic_user_id=AGENT_AUID,
    agentic_user_email=AGENT_EMAIL,
    agent_blueprint_id=BLUEPRINT_ID,
    tenant_id=TENANT_ID,
)

# Quem chamou o agente. E o que responde a pergunta "Who asked" no Defender.
detalhes_chamador = CallerDetails(
    user_details=UserDetails(
        user_id=CALLER_USER_ID,
        user_email=CALLER_USER_EMAIL,
        user_name=CALLER_USER_NAME,
    )
)


def _registrar_saida(pergunta, resposta, conversa):
    with OutputScope(
        request=_criar_request(pergunta, conversa),
        response=Response(messages=resposta),
        agent_details=detalhes_agente,
        user_details=detalhes_chamador.user_details,
    ):
        pass


@tool
def consultar_clima(cidade: str) -> str:
    """Retorna a temperatura atual de uma cidade.

    Cidades aceitas: Sao Paulo, Rio de Janeiro, Brasilia, Belo Horizonte,
    Porto Alegre, Recife, Salvador, Curitiba, Manaus, Fortaleza, Goiania,
    Belem, Vitoria, Florianopolis, Campinas, Lisboa, Porto, Madri, Londres,
    Paris, Nova York, Toquio, Seattle.
    Acentos e maiusculas sao ignorados.
    """
    detalhes = ToolCallDetails(
        tool_name="consultar_clima",
        arguments={"cidade": cidade},
        description="Consulta a temperatura atual via Open-Meteo",
        tool_type=TIPO_FERRAMENTA_FUNCAO,
        endpoint=ServiceEndpoint(hostname="api.open-meteo.com", port=443),
    )
    escopo = ExecuteToolScope(
        request=_criar_request(cidade),
        details=detalhes,
        agent_details=detalhes_agente,
    )
    with escopo:
        chave = _normalizar(cidade)
        coordenadas = CIDADES.get(chave)
        if coordenadas is None:
            disponiveis = ", ".join(sorted(CIDADES))
            resultado = (
                "Nao tenho as coordenadas de '" + cidade + "'. "
                "Cidades disponiveis: " + disponiveis
            )
        else:
            latitude, longitude = coordenadas
            resposta = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m",
                },
                timeout=15,
            )
            temperatura = resposta.json()["current"]["temperature_2m"]
            resultado = str(temperatura) + " graus Celsius em " + cidade + "."
        escopo.record_response(resultado)
    return resultado


agente = create_react_agent(llm, [consultar_clima])


# ---------------------------------------------------------------------------
# Chat interativo
# ---------------------------------------------------------------------------
AJUDA = """
Comandos:
  /sair     encerra o chat
  /novo     comeca uma conversa nova
  /purview  envia um prompt com dados sinteticos sensiveis,
            para demonstrar a deteccao no Microsoft Purview
  /ajuda    mostra esta mensagem

Experimente perguntar:
  Qual a temperatura em Sao Paulo?
  Compare o clima do Rio de Janeiro com o de Curitiba.
  Esta mais frio em Porto Alegre ou em Londres?
"""


# Bloco de dados FICTICIOS, usado apenas para acionar a deteccao de dados
# sensiveis no Microsoft Purview. Nenhum dado real de pessoa esta aqui.
PROMPT_PURVIEW = (
    "Analise estes registros de clientes e resuma quantos sao do Missouri. "
    "ssn gender birthdate lname fname address city state zip phone "
    "172-32-1176 m 1958/04/21 White Johnson 10932 Bigge Rd Menlo Park CA 94025 408-496-7223 "
    "514-14-8905 f 1944/12/22 Borden Ashley 4469 Sherman Street Goff KS 66428 785-939-6046 "
    "489-36-8350 m 1964/09/06 Aragon Robert 3181 White Oak Drive Kansas City MO 66215 816-645-6936 "
    "514-30-2668 f 1986/05/27 Russell Jacki 3097 Better Street Kansas City MO 66215 913-227-6106"
)


def mascarar(valor):
    if not valor or len(valor) < 8:
        return "****"
    return valor[:4] + "..." + valor[-4:]


def abrir_banner():
    print()
    print("=" * 68)
    print("  Microsoft Agent 365 - Agente de laboratorio")
    print("=" * 68)
    print("  Agente   :", AGENT_NAME)
    print("  Agent ID :", mascarar(AGENT_ID))
    print("  Tenant   :", mascarar(TENANT_ID))
    print("  Modelo   :", os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"])
    print("  Telemetria: Microsoft Agent 365 (app-only, S2S)")
    if PURVIEW_ENABLED:
        modo = "fail-closed" if PURVIEW_FAIL_CLOSED else "fail-open"
        print("  Purview   : avaliacao ativa (" + modo + ")")
    else:
        print("  Purview   : desativado (PURVIEW_ENABLED=false)")
    print("=" * 68)
    print(AJUDA)


def conversar():
    abrir_banner()

    avisos = _validar_atribuicao()
    if avisos:
        print("  Aviso: atribuicao do chamador incompleta ->", ", ".join(avisos))
        print("  O agente funciona, mas o Defender nao sabera quem pediu.")
        print()

    print("Validando credenciais...", end=" ", flush=True)
    try:
        token_resolver(AGENT_ID, TENANT_ID)
        print("ok")
    except Exception as erro:
        print("FALHOU")
        print()
        print(erro)
        print()
        print("Verifique o client secret no .env e se o comando")
        print("'a365 setup permissions bot' foi executado.")
        sys.exit(1)
    print()

    conversa = str(uuid.uuid4())
    historico = []
    sequencia = 0

    while True:
        try:
            pergunta = input("Voce > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not pergunta:
            continue
        if pergunta.lower() in ("/sair", "/exit", "/quit"):
            break
        if pergunta.lower() == "/ajuda":
            print(AJUDA)
            continue
        if pergunta.lower() == "/purview":
            pergunta = PROMPT_PURVIEW
            print()
            print("Enviando prompt com dados sinteticos sensiveis...")
            print("Verifique depois em Purview > DSPM > AI observability.")
            print()
        elif pergunta.lower() == "/novo":
            conversa = str(uuid.uuid4())
            historico = []
            sequencia = 0
            print("Conversa reiniciada.")
            print()
            continue

        token_conversa = _conversa_atual.set(conversa)
        escopo = InvokeAgentScope(
            request=_criar_request(pergunta, conversa),
            scope_details=InvokeAgentScopeDetails(),
            agent_details=detalhes_agente,
            caller_details=detalhes_chamador,
        )
        try:
            with escopo:
                escopo.record_input_messages(pergunta)

                # Purview avalia o PROMPT antes de chegar ao modelo.
                if purview_bloqueia(pergunta, "uploadText", conversa, sequencia):
                    escopo.record_output_messages(MSG_PROMPT_BLOQUEADO)
                    _registrar_saida(pergunta, MSG_PROMPT_BLOQUEADO, conversa)
                    print()
                    print("Agente >", MSG_PROMPT_BLOQUEADO)
                    print()
                    sequencia += 2
                    continue

                historico.append(("user", pergunta))
                resultado = agente.invoke({"messages": historico})
                historico = resultado["messages"]
                resposta = historico[-1].content

                # Purview avalia a RESPOSTA antes de chegar ao usuario.
                if purview_bloqueia(resposta, "downloadText", conversa, sequencia + 1):
                    resposta = MSG_RESPOSTA_BLOQUEADA
                    historico = historico[:-1]

                escopo.record_output_messages(resposta)
                _registrar_saida(pergunta, resposta, conversa)
                sequencia += 2
        except Exception as erro:
            print()
            print("Erro ao processar:", erro)
            print()
            continue
        finally:
            _conversa_atual.reset(token_conversa)

        print()
        print("Agente >", resposta)
        print()

    print("Enviando telemetria pendente...", end=" ", flush=True)
    provider.force_flush()
    print("ok")
    print("Ate logo.")


conversar()
