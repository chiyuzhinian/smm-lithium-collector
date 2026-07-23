param([string]$TaskName = "SMM_Lithium_Daily_Collector")
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
Write-Host "Scheduled task removed: $TaskName"
