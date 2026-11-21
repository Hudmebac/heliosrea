param(
  [Parameter(Mandatory = $true)][string]$Text,
  [Parameter(Mandatory = $true)][string]$Voice,
  [double]$Rate = 0.86,
  [double]$Pitch = 1.0,
  [Parameter(Mandatory = $true)][string]$Out
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media, ContentType = WindowsRuntime]
$null = [Windows.Media.SpeechSynthesis.SpeechSynthesisStream, Windows.Media, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq "AsTask" -and
  $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq "IAsyncOperation``1"
})[0]

function Await($WinRtTask, $ResultType) {
  $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
  $netTask = $asTask.Invoke($null, @($WinRtTask))
  $netTask.Wait(-1) | Out-Null
  $netTask.Result
}

$synth = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::new()
$selected = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices |
  Where-Object { $_.DisplayName -eq $Voice } |
  Select-Object -First 1
if (-not $selected) { throw "OneCore voice not found: $Voice" }

$synth.Voice = $selected
$synth.Options.SpeakingRate = [Math]::Max(0.5, [Math]::Min(2.0, $Rate))
$synth.Options.AudioPitch = [Math]::Max(0.5, [Math]::Min(1.5, $Pitch))
$stream = Await ($synth.SynthesizeTextToStreamAsync($Text)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
$reader = [Windows.Storage.Streams.DataReader]::new($stream.GetInputStreamAt(0))
$null = Await ($reader.LoadAsync([uint32]$stream.Size)) ([uint32])
$bytes = New-Object byte[] $stream.Size
$reader.ReadBytes($bytes)
[System.IO.File]::WriteAllBytes($Out, $bytes)
$reader.Dispose()
$stream.Dispose()
Write-Output ("rendered " + $selected.DisplayName + " rate=" + $Rate + " pitch=" + $Pitch)
