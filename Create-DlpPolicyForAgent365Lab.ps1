<#
.SYNOPSIS
Cria uma politica de DLP (Data Loss Prevention) no Microsoft Purview que bloqueia
informacoes sensiveis nos prompts enviados ao agente do laboratorio Agent 365.

.DESCRIPTION
Baseado no exemplo oficial da Microsoft para DLP em apps de IA customizados:
https://github.com/microsoft/purview-api-samples/tree/main/DLPforCustomAIApps

A politica e escopada ao registro do app/agente no Microsoft Entra ID via o
enforcement plane "Application" (substitui o antigo "Entra"). A regra bloqueia
a acao UploadText quando o conteudo contem os tipos de informacao sensivel
listados abaixo.

O portal do Purview NAO permite criar esta politica: a location "Managed cloud
apps" aparece sem caixa de selecao, bloqueada pelos pre-requisitos de
Conditional Access e Edge for Business. Este script e o caminho suportado.

.NOTES
Pre-requisitos:
- PowerShell 7+
- Modulo ExchangeOnlineManagement (Install-Module ExchangeOnlineManagement -Scope CurrentUser)
- Role "Compliance Administrator" ou "Compliance Data Administrator" no Purview
- Microsoft Purview Audit habilitado no tenant

Referencias:
- New-DlpCompliancePolicy: https://learn.microsoft.com/powershell/module/exchange/new-dlpcompliancepolicy
- New-DlpComplianceRule:   https://learn.microsoft.com/powershell/module/exchange/new-dlpcompliancerule
- Connect-IPPSSession:     https://learn.microsoft.com/powershell/exchange/connect-to-scc-powershell
- Guia oficial (DSPM for AI, custom apps): https://learn.microsoft.com/purview/developer/configurepurview
#>

$ErrorActionPreference = "Stop"

# -----------------------------------------------------------------------------
# Configuracao - substitua os valores marcados com <...> antes de rodar.
# -----------------------------------------------------------------------------

# Nomes exibidos no portal do Purview.
# ATENCAO: a exclusao de politica no Purview e ASSINCRONA. Depois de remover,
# o Get-DlpCompliancePolicy ainda devolve o objeto marcado como deletado por
# um tempo, e qualquer Set- nele falha com ErrorCompliancePolicyIsDeletedException.
# Se precisar recomecar do zero, use um NOME NOVO em vez de esperar a exclusao.
$DlpPolicyName = "A365 Lab - Block Sensitive Data"
$DlpRuleName   = "Bloquear info sensivel no agente"

# Remover regra + politica com estes nomes antes de criar.
# Mantenha $false quando estiver usando um nome novo (recomendado).
# Use $true apenas para limpar o ambiente, sem criar em seguida.
$RecreateFromScratch = $false

# Modo de aplicacao da politica:
#   Enable                    -> politica ativa, acoes sao de fato aplicadas
#   TestWithNotifications      -> modo de teste com notificacoes
#   TestWithoutNotifications   -> modo de teste sem notificacoes
#   Disable                    -> politica existe mas nao roda
$PolicyMode = "Enable"

# Acao da regra em caso de match. "Block" nega a requisicao, "Audit" so registra.
$RestrictAction = "Block"

# Quais atividades restringir.
# ATENCAO: para o enforcement plane "Application", apenas "UploadText" e
# aceito de forma confiavel -- e o que o exemplo oficial da Microsoft usa.
# Incluir "DownloadText" faz o Purview rejeitar com
# DlpRestrictionIsNotSupportedException dependendo dos apps no escopo.
# UploadText cobre o cenario: bloquear o PROMPT com dados sensiveis.
$RestrictSettings = @("UploadText")

# App(s) a escopar. AppId = Application (client) ID do registro no Entra.
#
# ATENCAO -- ponto critico:
# O Purview so aplica a politica ao app que o CODIGO declara em
# protectedAppMetadata.applicationLocation (e no protectionScopes/compute).
# No agent.py esse valor e o PURVIEW_CLIENT_APP_ID -- o app registration usado
# para autenticar na API do Purview, NAO o blueprint do agente.
#
# Se a politica escopar apenas o blueprint, o Purview nao encontra politica
# aplicavel e libera o conteudo silenciosamente, sem erro algum no log.
#
# Por isso escopamos os dois:
$Applications = @(
    # O que o codigo declara -- obrigatorio para o bloqueio funcionar.
    # Mesmo valor de PURVIEW_CLIENT_APP_ID no seu .env
    @{ AppId = "<APP-ID-DO-CLIENTE-PURVIEW>"; AppName = "A365 Lab Purview Client" },

    # O blueprint do agente -- governanca por agente.
    # Obtido com: a365 setup all (campo blueprint id)
    @{ AppId = "<APP-ID-DO-BLUEPRINT>";       AppName = "LabWeatherAgent Blueprint" }
)

# Destinatarios de relatorio de incidente e notificacao ao usuario.
# "SiteAdmin" (admins de compliance do tenant) e aceito em
# GenerateIncidentReport, mas o parametro NotifyUser (notificacao ao usuario
# final) exige um endereco SMTP valido -- por isso o e-mail direto.
$IncidentRecipients = @("SiteAdmin")
$NotifyRecipients   = @("<seu-email@seu-dominio.com>")

# Tipos de informacao sensivel a detectar.
# Os nomes precisam bater com os SITs do Microsoft Purview.
$SensitiveTypes = @(
    @{ Name = "U.S. Social Security Number (SSN)" },
    @{ Name = "All Full Names" },
    @{ Name = "All Physical Addresses" }
)

# -----------------------------------------------------------------------------
# Validacao da configuracao
# -----------------------------------------------------------------------------
$placeholders = @()
foreach ($app in $Applications) {
    if ($app.AppId -like "<*>") { $placeholders += $app.AppId }
}
foreach ($mail in $NotifyRecipients) {
    if ($mail -like "<*>") { $placeholders += $mail }
}
if ($placeholders.Count -gt 0) {
    throw "Substitua os placeholders antes de rodar: $($placeholders -join ', ')"
}

# -----------------------------------------------------------------------------
# Conectar ao Security & Compliance PowerShell
# -----------------------------------------------------------------------------
Write-Host "Conectando ao Security & Compliance PowerShell..." -ForegroundColor Cyan
if (-not (Get-Module -ListAvailable -Name ExchangeOnlineManagement)) {
    throw "Modulo ExchangeOnlineManagement nao instalado. Rode: Install-Module ExchangeOnlineManagement -Scope CurrentUser"
}
Import-Module ExchangeOnlineManagement -ErrorAction Stop
Connect-IPPSSession

# -----------------------------------------------------------------------------
# Limpeza previa (quando $RecreateFromScratch = $true)
# -----------------------------------------------------------------------------
if ($RecreateFromScratch) {
    Write-Host "Removendo regra e politica anteriores (se existirem)..." -ForegroundColor Yellow

    if (Get-DlpComplianceRule -Identity $DlpRuleName -ErrorAction SilentlyContinue) {
        Remove-DlpComplianceRule -Identity $DlpRuleName -Confirm:$false -ErrorAction Stop
        Write-Host "  Regra anterior removida." -ForegroundColor DarkGray
    }

    if (Get-DlpCompliancePolicy -Identity $DlpPolicyName -ErrorAction SilentlyContinue) {
        Remove-DlpCompliancePolicy -Identity $DlpPolicyName -Confirm:$false -ErrorAction Stop
        Write-Host "  Politica anterior removida." -ForegroundColor DarkGray
    }

    Start-Sleep -Seconds 5
}

# -----------------------------------------------------------------------------
# Montar o JSON de Locations que escopa a politica ao(s) app(s) do Entra.
# -----------------------------------------------------------------------------
$LocationsObject = @(
    foreach ($app in $Applications) {
        @{
            Workload            = "Applications"
            Location            = $app.AppId
            LocationDisplayName = $app.AppName
            LocationSource      = "Entra"
            LocationType        = "Individual"
            Inclusions          = @(@{ Type = "Tenant"; Identity = "All" })
        }
    }
)
# -AsArray forca a serializacao como array JSON mesmo com 1 unico elemento.
# Sem isso, o PowerShell "desembrulha" arrays de 1 item e o ConvertTo-Json
# gera um objeto {} em vez de [{}], que a API do Purview rejeita com
# "Locations input JSON is invalid ... requires a JSON array".
$LocationsJson = $LocationsObject | ConvertTo-Json -Depth 6 -Compress -AsArray

# -----------------------------------------------------------------------------
# Criar ou atualizar a politica de DLP
# -----------------------------------------------------------------------------
Write-Host "Garantindo que a politica de DLP '$DlpPolicyName' existe..." -ForegroundColor Yellow
$existingPolicy = Get-DlpCompliancePolicy -Identity $DlpPolicyName -ErrorAction SilentlyContinue

# Uma politica recem-excluida ainda aparece no Get- por um tempo, mas nao pode
# ser modificada. Detectamos isso cedo para dar uma mensagem acionavel.
if ($existingPolicy -and $existingPolicy.Mode -like "*PendingDeletion*") {
    throw "A politica '$DlpPolicyName' foi excluida e ainda esta sendo removida pelo servico. Troque `$DlpPolicyName e `$DlpRuleName para um nome novo e rode de novo."
}

# -ErrorAction Stop e obrigatorio: sem ele, um erro de binding de parametro nao
# interrompe o script, que segue imprimindo "criada" sobre algo que falhou.
if (-not $existingPolicy) {
    New-DlpCompliancePolicy `
        -Name $DlpPolicyName `
        -Comment "Bloqueia tipos comuns de informacao sensivel no agente (Agent 365)." `
        -Locations $LocationsJson `
        -EnforcementPlanes @("Application") `
        -Mode $PolicyMode `
        -ErrorAction Stop | Out-Null
    Write-Host "Politica de DLP criada." -ForegroundColor Green
} else {
    Set-DlpCompliancePolicy `
        -Identity $DlpPolicyName `
        -Locations $LocationsJson `
        -EnforcementPlanes @("Application") `
        -Mode $PolicyMode `
        -ErrorAction Stop | Out-Null
    Write-Host "Politica de DLP atualizada." -ForegroundColor Green
}

# -----------------------------------------------------------------------------
# Criar ou atualizar a regra de DLP
# -----------------------------------------------------------------------------
Write-Host "Garantindo que a regra de DLP '$DlpRuleName' existe..." -ForegroundColor Yellow
$existingRule = Get-DlpComplianceRule -Identity $DlpRuleName -ErrorAction SilentlyContinue

$restrictAccessList = @(
    foreach ($setting in $RestrictSettings) {
        @{ Setting = $setting; Value = $RestrictAction }
    }
)

# Parametros alinhados ao exemplo oficial do New-DlpComplianceRule para o
# cenario "entra-registered enterprise application".
# GenerateAlert e booleano aqui, nao uma lista de destinatarios.
# IncidentReportContent, ReportSeverityLevel e StopPolicyProcessing NAO sao
# suportados para a location "Applications".
$ruleParams = @{
    ContentContainsSensitiveInformation = $SensitiveTypes
    GenerateAlert           = $true
    GenerateIncidentReport  = $IncidentRecipients
    NotifyUser              = $NotifyRecipients
    RestrictAccess          = $restrictAccessList
    Comment = "Bloqueia prompts que contenham os tipos de informacao sensivel listados."
}

if (-not $existingRule) {
    New-DlpComplianceRule -Name $DlpRuleName -Policy $DlpPolicyName @ruleParams -ErrorAction Stop | Out-Null
    Write-Host "Regra de DLP criada." -ForegroundColor Green
} else {
    Set-DlpComplianceRule -Identity $DlpRuleName @ruleParams -ErrorAction Stop | Out-Null
    Write-Host "Regra de DLP atualizada." -ForegroundColor Green
}

# -----------------------------------------------------------------------------
# Verificacao
# -----------------------------------------------------------------------------
Write-Host "`nVerificacao:" -ForegroundColor Cyan
Get-DlpCompliancePolicy -Identity $DlpPolicyName | Format-List Name, Mode, EnforcementPlanes, Locations
Get-DlpComplianceRule -Identity $DlpRuleName | Format-List Name, Policy, RestrictAccess, Disabled

Write-Host "`nAgora rode o agente e use /purview para testar o bloqueio." -ForegroundColor Cyan
Write-Host "Reinicie o agente antes: o ETag do protectionScopes fica 1h em cache." -ForegroundColor DarkGray

# -----------------------------------------------------------------------------
# Limpeza (rode manualmente se precisar remover a politica depois)
# -----------------------------------------------------------------------------
# Remove-DlpComplianceRule -Identity $DlpRuleName -Confirm:$false
# Remove-DlpCompliancePolicy -Identity $DlpPolicyName -Confirm:$false
