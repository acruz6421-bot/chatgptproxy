param([int]$Port = 3535)
# Encerra qualquer processo escutando na porta $Port (resíduos do chatgptproxy)
$killed = 0
try {
  $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
  $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($procId in $pids) {
    try {
      $p = Get-Process -Id $procId -ErrorAction Stop
      Write-Host ("Porta {0} ocupada por {1} (PID {2}) - encerrando" -f $Port, $p.ProcessName, $procId)
      Stop-Process -Id $procId -Force -ErrorAction Stop
      $killed++
    } catch {}
  }
} catch {
  Write-Host ("Porta {0} esta livre." -f $Port)
}
if ($killed -gt 0) { Start-Sleep -Seconds 1; Write-Host "Porta $Port liberada ($killed processo(s))." }
