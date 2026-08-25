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
import time
import unicodedata
import uuid

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
    Request,
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


@tool
def consultar_clima(cidade: str) -> str:
    """Retorna a temperatura atual de uma cidade.

    Cidades aceitas: Sao Paulo, Rio de Janeiro, Brasilia, Belo Horizonte,
    Porto Alegre, Recife, Salvador, Curitiba, Manaus, Fortaleza, Goiania,
    Belem, Vitoria, Florianopolis, Campinas, Lisboa, Porto, Madri, Londres,
    Paris, Nova York, Toquio, Seattle.
    Acentos e maiusculas sao ignorados.
    """
    chave = _normalizar(cidade)
    coordenadas = CIDADES.get(chave)
    if coordenadas is None:
        disponiveis = ", ".join(sorted(CIDADES))
        return (
            "Nao tenho as coordenadas de '" + cidade + "'. "
            "Cidades disponiveis: " + disponiveis
        )

    latitude, longitude = coordenadas
    detalhes = ToolCallDetails(
        tool_name="consultar_clima",
        arguments={"cidade": cidade},
        description="Consulta a temperatura atual via Open-Meteo",
        tool_type=TIPO_FERRAMENTA_FUNCAO,
        endpoint=ServiceEndpoint(hostname="api.open-meteo.com", port=443),
    )
    with ExecuteToolScope(
        request=Request(content=cidade),
        details=detalhes,
        agent_details=detalhes_agente,
    ):
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
        return str(temperatura) + " graus Celsius em " + cidade + "."


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
            print("Conversa reiniciada.")
            print()
            continue

        escopo = InvokeAgentScope(
            request=Request(content=pergunta, conversation_id=conversa),
            scope_details=InvokeAgentScopeDetails(),
            agent_details=detalhes_agente,
            caller_details=detalhes_chamador,
        )
        try:
            with escopo:
                escopo.record_input_messages(pergunta)
                historico.append(("user", pergunta))
                resultado = agente.invoke({"messages": historico})
                historico = resultado["messages"]
                resposta = historico[-1].content
                escopo.record_output_messages(resposta)
        except Exception as erro:
            print()
            print("Erro ao processar:", erro)
            print()
            continue

        print()
        print("Agente >", resposta)
        print()

    print("Enviando telemetria pendente...", end=" ", flush=True)
    provider.force_flush()
    print("ok")
    print("Ate logo.")


conversar()
