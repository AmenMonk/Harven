Add-Type -AssemblyName System.Speech
$text = [System.IO.File]::ReadAllText('H:\HarvenAI\tts_text.txt', [System.Text.Encoding]::UTF8)
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voices = $speaker.GetInstalledVoices()
foreach ($v in $voices) {
    if ($v.VoiceInfo.Name -like '*Huihui*') {
        $speaker.SelectVoice($v.VoiceInfo.Name)
        break
    }
}
$speaker.Speak($text)
Write-Output 'TTS_DONE'
