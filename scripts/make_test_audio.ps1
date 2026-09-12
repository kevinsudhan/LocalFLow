# Generate spoken WAV fixtures with the Windows speech synthesiser.
#
# These give the test suite real audio - not synthetic tones - so the ASR,
# VAD and end-to-end pipeline tests exercise the same code path a microphone
# would. Synthesised speech is cleaner than a real voice, so treat the word
# error rates measured here as a floor, not a prediction.
#
#   powershell -ExecutionPolicy Bypass -File scripts/make_test_audio.ps1

param(
    [string]$OutDir = "$PSScriptRoot\..\tests\fixtures\audio",
    [string]$Voice = ""
)

Add-Type -AssemblyName System.Speech
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$phrases = [ordered]@{
    "normal"          = "Can you send Rahul the quotation on Friday."
    "fillers"         = "Um, so, basically I think we should, uh, ship the order today."
    "correction"      = "I will send it Monday. Actually Tuesday."
    "correction_name" = "Send it to Rahul. No, send it to Karthik."
    "numbers"         = "The total is four thousand five hundred rupees, due on the fifteenth of March."
    "list"            = "Things I need to do. One, finish the C R M. Two, fix Outlook. Three, test the parser."
    "technical"       = "Please update the fast API endpoint and redeploy the container."
    "email"           = "Hi Rahul, I wanted to let you know that customs has delayed the shipment by two days."
    "question"        = "Can you send this today?"
    "long"            = "Good morning everyone. I wanted to give a quick update on the shipment status. The container cleared customs yesterday evening and is now on its way to the warehouse. We expect delivery by Thursday afternoon. I will share the tracking details shortly."
    "silence_lead"    = "The shipment has been delayed."
}

$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($Voice -ne "") {
    try { $synth.SelectVoice($Voice) } catch { Write-Warning "Voice '$Voice' unavailable; using the default." }
}
$synth.Rate = 0
$synth.Volume = 100

Write-Host "Installed voices:"
$synth.GetInstalledVoices() | ForEach-Object { Write-Host "  - $($_.VoiceInfo.Name)" }

$manifest = @{}
foreach ($name in $phrases.Keys) {
    $text = $phrases[$name]
    $path = Join-Path $OutDir "$name.wav"
    $synth.SetOutputToWaveFile($path)
    if ($name -eq "silence_lead") {
        # Leading and trailing silence, so the VAD trimmer has something to do.
        $synth.SpeakSsml("<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'><break time='1200ms'/>$text<break time='1000ms'/></speak>")
    } else {
        $synth.Speak($text)
    }
    $synth.SetOutputToNull()
    $manifest[$name] = $text
    Write-Host ("  wrote {0,-16} {1}" -f "$name.wav", $text)
}
$synth.Dispose()

$manifest | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $OutDir "manifest.json")
Write-Host "`nWrote $($phrases.Count) fixtures to $OutDir"
