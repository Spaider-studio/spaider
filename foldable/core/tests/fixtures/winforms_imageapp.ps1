# Synthetic image-based test window (no accessible text for the picture):
# -Kind image : shows the PNG given by -Image in a PictureBox (chart / error screenshot)
# -Kind canvas: paints a green "WEITER" button without any UIA control; a click on it
#               sets the status label (read through UIA) to "Weiter gedrückt".
param(
    [string]$Title = "SpAIder Bildfenster",
    [string]$Kind = "image",
    [string]$Image = "",
    [int]$AutoCloseSeconds = 600
)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object System.Windows.Forms.Form
$form.Text = $Title
$form.Width = 820
$form.Height = 560
$form.StartPosition = "CenterScreen"

$status = New-Object System.Windows.Forms.Label
$status.Text = "Bereit"
$status.Left = 20; $status.Top = 480; $status.Width = 700
$status.Name = "labelStatus"
$form.Controls.Add($status)

if ($Kind -eq "image") {
    $pb = New-Object System.Windows.Forms.PictureBox
    $pb.Left = 10; $pb.Top = 10; $pb.Width = 780; $pb.Height = 460
    $pb.SizeMode = "Zoom"
    $pb.AccessibleName = "Bild"
    if ($Image -and (Test-Path $Image)) { $pb.Image = [System.Drawing.Image]::FromFile($Image) }
    $form.Controls.Add($pb)
} else {
    $panel = New-Object System.Windows.Forms.Panel
    $panel.Left = 10; $panel.Top = 10; $panel.Width = 780; $panel.Height = 460
    $panel.BackColor = [System.Drawing.Color]::FromArgb(240, 242, 246)
    $script:btn = New-Object System.Drawing.Rectangle(480, 250, 180, 56)
    $panel.Add_Paint({
        param($s, $e)
        $g = $e.Graphics
        $g.FillRectangle([System.Drawing.Brushes]::SteelBlue, 0, 0, 780, 40)
        $g.DrawString("Assistent (gezeichnete Oberfläche)", (New-Object System.Drawing.Font("Segoe UI", 12)), [System.Drawing.Brushes]::White, 10, 10)
        $g.FillRectangle((New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(34, 139, 84))), $script:btn)
        $g.DrawRectangle((New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(10, 80, 40), 3)), $script:btn)
        $g.DrawString("WEITER", (New-Object System.Drawing.Font("Segoe UI", 14, [System.Drawing.FontStyle]::Bold)), [System.Drawing.Brushes]::White, 525, 263)
        $g.FillRectangle([System.Drawing.Brushes]::LightGray, 60, 250, 180, 56)
        $g.DrawString("ZURÜCK", (New-Object System.Drawing.Font("Segoe UI", 14, [System.Drawing.FontStyle]::Bold)), [System.Drawing.Brushes]::DimGray, 100, 263)
    })
    $panel.Add_MouseClick({
        param($s, $e)
        if ($script:btn.Contains($e.Location)) { $status.Text = "Weiter gedrückt" } else { $status.Text = "Daneben geklickt (" + $e.X + "," + $e.Y + ")" }
    })
    $form.Controls.Add($panel)
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = [Math]::Max(1000, $AutoCloseSeconds * 1000)
$timer.Add_Tick({ $form.Close() })
$timer.Start()
[void]$form.ShowDialog()
