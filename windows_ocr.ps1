param(
  [Parameter(Mandatory=$true)] [string]$ImagePath,
  [string]$Language = "zh-Hans",
  [string]$OutputPath = ""
)
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapPixelFormat, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapAlphaMode, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
function Await($Op, $Type) {
  $m = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq "AsTask" -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq "IAsyncOperation``1" } | Select-Object -First 1
  $t = $m.MakeGenericMethod($Type).Invoke($null, @($Op))
  $t.Wait()
  $t.Result
}
$f = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync([System.IO.Path]::GetFullPath($ImagePath))) ([Windows.Storage.StorageFile])
$s = Await ($f.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStream])
$d = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($s)) ([Windows.Graphics.Imaging.BitmapDecoder])
$b = Await ($d.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
try {
  $b = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert($b, [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8, [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)
} catch {}
$engine = $null
try { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($Language)) } catch {}
if ($null -eq $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if ($null -eq $engine) { throw "Windows OCR is not available" }
$r = Await ($engine.RecognizeAsync($b)) ([Windows.Media.Ocr.OcrResult])
$outLines = New-Object System.Collections.Generic.List[string]
foreach ($line in $r.Lines) {
  $words = @()
  foreach ($word in $line.Words) { $words += $word.Text }
  $joined = ($words -join " ").Trim()
  if ($joined.Length -gt 0) { $outLines.Add($joined) }
}
$text = [string]::Join([Environment]::NewLine, $outLines)
if ($OutputPath -ne "") {
  $utf8 = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText([System.IO.Path]::GetFullPath($OutputPath), $text, $utf8)
} else {
  [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
  Write-Output $text
}
