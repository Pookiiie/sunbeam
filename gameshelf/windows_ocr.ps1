# Reads text from an image with Windows' built-in OCR (Windows 10/11, no install needed).
# Prints JSON: {"width":..,"height":..,"lines":[{"words":[{"t","x","y","w","h"}]}]}
param([Parameter(Mandatory = $true)][string]$Path)

$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]

    $asTaskGeneric = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1

    function Await($op, [Type]$resultType) {
        $task = $asTaskGeneric.MakeGenericMethod($resultType).Invoke($null, @($op))
        $task.Wait(-1) | Out-Null
        return $task.Result
    }

    $engine = $null
    try { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('en-US')) } catch {}
    if ($null -eq $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
    if ($null -eq $engine) { throw 'No Windows text-recognition language is installed (Settings > Time & language > Language).' }

    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])

    # Phone screenshots can be taller than the OCR engine accepts: scale down if needed.
    $max = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
    $w = $decoder.PixelWidth; $h = $decoder.PixelHeight
    $scale = [Math]::Min(1.0, $max / [Math]::Max($w, $h))
    if ($scale -lt 1.0) {
        $transform = New-Object Windows.Graphics.Imaging.BitmapTransform
        $transform.ScaledWidth = [uint32][Math]::Floor($w * $scale)
        $transform.ScaledHeight = [uint32][Math]::Floor($h * $scale)
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync(
            [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8, [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied,
            $transform, [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation,
            [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $w = $transform.ScaledWidth; $h = $transform.ScaledHeight
    } else {
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    }

    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $lines = @()
    foreach ($line in $result.Lines) {
        $words = @()
        foreach ($word in $line.Words) {
            $r = $word.BoundingRect
            $words += [pscustomobject]@{ t = $word.Text; x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height }
        }
        $lines += [pscustomobject]@{ words = $words }
    }
    $stream.Dispose()
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [pscustomobject]@{ width = [int]$w; height = [int]$h; lines = $lines } | ConvertTo-Json -Depth 6 -Compress
}
catch {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [pscustomobject]@{ error = "$($_.Exception.Message)" } | ConvertTo-Json -Compress
}
