[CmdletBinding()]
param(
    [string]$MasterHost,
    [ValidateRange(1, 65535)]
    [int]$MasterPort = 5000,
    [int]$TcpTimeoutMilliseconds = 3000
)

$ErrorActionPreference = 'Stop'
$startedAt = [DateTime]::UtcNow
$outputDirectory = Join-Path (Get-Location) 'artifacts\phase1'
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

function Test-PrivateIPv4([string]$Address) {
    $ip = $null
    if (-not [System.Net.IPAddress]::TryParse($Address, [ref]$ip)) { return $false }
    if ($ip.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { return $false }
    $bytes = $ip.GetAddressBytes()
    return (($bytes[0] -eq 10) -or
        ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or
        ($bytes[0] -eq 192 -and $bytes[1] -eq 168))
}

$os = Get-CimInstance Win32_OperatingSystem
$computer = Get-CimInstance Win32_ComputerSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$adapters = Get-NetIPConfiguration | Where-Object { $_.IPv4Address }
$interfaces = foreach ($adapter in $adapters) {
    $ipv4 = @($adapter.IPv4Address | ForEach-Object { $_.IPAddress })
    [ordered]@{
        interface = $adapter.InterfaceAlias
        index = $adapter.InterfaceIndex
        status = (Get-NetAdapter -InterfaceIndex $adapter.InterfaceIndex -ErrorAction SilentlyContinue).Status
        ipv4 = $ipv4
        private_ipv4 = @($ipv4 | Where-Object { Test-PrivateIPv4 $_ })
        prefix_lengths = @($adapter.IPv4Address | ForEach-Object { $_.PrefixLength })
        gateway = @($adapter.IPv4DefaultGateway | ForEach-Object { $_.NextHop })
        dns = @($adapter.DnsServer.ServerAddresses)
    }
}

$tcp = [ordered]@{ state = 'not_requested'; host = $MasterHost; port = $MasterPort; latency_ms = $null; detail = $null }
if ($MasterHost) {
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connectTask = $client.ConnectAsync($MasterHost, $MasterPort)
        if (-not $connectTask.Wait($TcpTimeoutMilliseconds)) {
            $tcp.state = 'timeout'
            $tcp.detail = "TCP connection timed out after $TcpTimeoutMilliseconds ms."
        } elseif ($client.Connected) {
            $tcp.state = 'succeeded'
            $tcp.detail = 'TCP connection succeeded.'
        } else {
            $tcp.state = 'failed'
            $tcp.detail = 'TCP connection failed; inspect endpoint and firewall policy.'
        }
        $watch.Stop()
        $tcp.latency_ms = [Math]::Round($watch.Elapsed.TotalMilliseconds, 2)
    } catch {
        $watch.Stop()
        $tcp.state = 'error'
        $tcp.latency_ms = [Math]::Round($watch.Elapsed.TotalMilliseconds, 2)
        $tcp.detail = $_.Exception.Message
    } finally {
        $client.Dispose()
    }
}

$gpuCandidates = @(Get-CimInstance Win32_VideoController | ForEach-Object {
    [ordered]@{
        name = $_.Name
        vendor = $_.AdapterCompatibility
        driver_version = $_.DriverVersion
        driver_date = $_.DriverDate
        video_memory_bytes = $_.AdapterRAM
        utilization = $null
        utilization_status = 'unavailable; requires a verified backend adapter'
    }
})

$python = Get-Command python -ErrorAction SilentlyContinue
$report = [ordered]@{
    schema_version = 'phase1.audit.v1'
    started_at_utc = $startedAt.ToString('o')
    completed_at_utc = [DateTime]::UtcNow.ToString('o')
    hostname = $env:COMPUTERNAME
    operating_system = [ordered]@{ caption = $os.Caption; version = $os.Version; build = $os.BuildNumber }
    cpu = [ordered]@{ name = $cpu.Name; logical_processors = $computer.NumberOfLogicalProcessors; physical_memory_bytes = $computer.TotalPhysicalMemory }
    python = [ordered]@{ available = [bool]$python; path = if ($python) { $python.Source } else { $null }; version = if ($python) { (& $python.Source --version 2>&1 | Out-String).Trim() } else { $null } }
    network_interfaces = @($interfaces)
    routes = @(Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object DestinationPrefix,NextHop,InterfaceAlias,RouteMetric)
    master_tcp_test = $tcp
    gpus = $gpuCandidates
    opencl = [ordered]@{ state = 'not_run'; detail = 'OpenCL enumeration and known-answer kernel execution are implemented in the Worker Agent qualification phase.' }
    notes = @('Historical IP addresses are not used as current configuration.', 'GPU utilization and OpenCL support are not inferred from adapter names or driver package presence.')
}

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$reportPath = Join-Path $outputDirectory "audit-$($env:COMPUTERNAME)-$timestamp.json"
$report | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -Path $reportPath
$report | ConvertTo-Json -Depth 8
Write-Host "Report written to $reportPath"