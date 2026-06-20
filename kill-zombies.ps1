param([string]$ProfileName = 'chatgpt_profile')
# Encerra qualquer chrome.exe segurando a pasta do perfil chatgpt_profile (login helper)
$procs = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
  Where-Object { $_.CommandLine -like "*$ProfileName*" }
$killed = 0
foreach ($p in $procs) {
  try {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
    $killed++
  } catch {}
}
Write-Host "Encerrou $killed processos chrome.exe ($ProfileName)"
Start-Sleep -Seconds 1
