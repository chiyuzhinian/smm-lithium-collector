param([string]$TaskName = "SMM_Lithium_Daily_Collector")
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$info = Get-ScheduledTaskInfo -TaskName $TaskName
$task | Select-Object TaskName,State
$info | Select-Object LastRunTime,LastTaskResult,NextRunTime,NumberOfMissedRuns

