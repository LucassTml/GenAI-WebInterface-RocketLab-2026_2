# Inicia o servidor do Ollama e garante que o modelo do .env esteja baixado.
#
#   powershell -ExecutionPolicy Bypass -File scripts\iniciar_ollama.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\iniciar_ollama.ps1 -Modelo qwen3:4b
#
# Procura o Ollama instalado (PATH ou %LOCALAPPDATA%\Programs\Ollama) e, se não
# achar, a versão portátil na pasta ..\ollama-portable (ao lado do projeto).
param([string]$Modelo = "", [switch]$SemBaixar)

$raiz = Split-Path -Parent $PSScriptRoot
# 127.0.0.1 em vez de localhost: no Windows o "localhost" tenta IPv6 primeiro e
# o Invoke-WebRequest estourava o timeout antes de cair pro IPv4
$url = "http://127.0.0.1:11434/api/version"

function Testar-Ollama {
    try { Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 5 | Out-Null; return $true } catch { return $false }
}

# modelo: parâmetro > OLLAMA_MODELO do .env > padrão
if (-not $Modelo) {
    $linha = Get-Content (Join-Path $raiz ".env") -ErrorAction SilentlyContinue | Where-Object { $_ -match '^\s*OLLAMA_MODELO\s*=' }
    if ($linha) { $Modelo = (($linha -split '=', 2)[1]).Split(',')[0].Trim() }
    if (-not $Modelo) { $Modelo = "qwen2.5:3b" }
}

# onde está o executável
$exe = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $exe) {
    $candidatos = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"),
        (Join-Path (Split-Path -Parent $raiz) "ollama-portable\ollama.exe")
    )
    $exe = $candidatos | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $exe) {
    Write-Host "Ollama não encontrado. Instale em https://ollama.com/download e rode de novo." -ForegroundColor Red
    exit 1
}

# a versão portátil guarda os modelos na pasta dela
if ($exe -like "*ollama-portable*") { $env:OLLAMA_MODELS = Join-Path (Split-Path -Parent $exe) "models" }
# o prompt do agente + resultado da consulta passam de 4k tokens (padrão numa GPU de 4 GB)
$env:OLLAMA_CONTEXT_LENGTH = "8192"

if (Testar-Ollama) {
    Write-Host "Ollama já está rodando."
} else {
    Write-Host "Iniciando o Ollama ($exe)..."
    Start-Process -FilePath $exe -ArgumentList "serve" -WindowStyle Hidden
    # na primeira vez ele demora ~35 s detectando a GPU
    Write-Host "Aguardando o servidor subir (pode levar até 1 minuto)..."
    for ($i = 0; $i -lt 90 -and -not (Testar-Ollama); $i++) { Start-Sleep -Seconds 1 }
    if (-not (Testar-Ollama)) { Write-Host "O Ollama não respondeu em 90 s." -ForegroundColor Red; exit 1 }
}

$baixados = & $exe list | Out-String
if ($baixados -notmatch [regex]::Escape($Modelo)) {
    if ($SemBaixar) { Write-Host "O modelo $Modelo ainda não foi baixado." -ForegroundColor Yellow; exit 1 }
    Write-Host "Baixando o modelo $Modelo (só na primeira vez)..."
    & $exe pull $Modelo
}
Write-Host "Pronto: $Modelo disponível em http://localhost:11434" -ForegroundColor Green
