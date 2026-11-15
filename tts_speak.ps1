Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voices = $speaker.GetInstalledVoices()
$zhVoice = $null
foreach ($v in $voices) {
    if ($v.VoiceInfo.Name -like '*Huihui*') { $zhVoice = $v.VoiceInfo.Name }
}
if ($zhVoice) { $speaker.SelectVoice($zhVoice) }
$text = [System.IO.File]::ReadAllText('H:\HarvenAI\tts_text.txt', [System.Text.Encoding]::UTF8)
$speaker.Speak($text)
Write-Output 'TTS_DONE'
