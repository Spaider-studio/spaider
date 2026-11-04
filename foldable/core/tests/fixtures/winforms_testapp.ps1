# Synthetic Windows reference application with real UI Automation controls
# (WinForms): a title label, a name field, a multi-line notes field and a
# "Speichern" button that writes both fields to the file given as -OutFile.
# Used by the real windows-tool tests; closes itself on -AutoCloseSeconds.
param(
    [string]$OutFile = "$env:TEMP\spaider-testapp-output.txt",
    [string]$Title = "SpAIder Testanwendung",
    [int]$AutoCloseSeconds = 300
)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object System.Windows.Forms.Form
$form.Text = $Title
$form.Width = 640
$form.Height = 420
$form.StartPosition = "CenterScreen"
$form.TopMost = $false

$label = New-Object System.Windows.Forms.Label
$label.Text = "Name:"
$label.Left = 20; $label.Top = 20; $label.Width = 120
$label.Name = "labelName"
$form.Controls.Add($label)

$name = New-Object System.Windows.Forms.TextBox
$name.Left = 150; $name.Top = 18; $name.Width = 440
$name.Name = "textName"
$name.AccessibleName = "Name"
$form.Controls.Add($name)

$label2 = New-Object System.Windows.Forms.Label
$label2.Text = "Notizen:"
$label2.Left = 20; $label2.Top = 60; $label2.Width = 120
$form.Controls.Add($label2)

$notes = New-Object System.Windows.Forms.TextBox
$notes.Multiline = $true
$notes.ScrollBars = "Vertical"
$notes.Left = 150; $notes.Top = 58; $notes.Width = 440; $notes.Height = 220
$notes.Name = "textNotes"
$notes.AccessibleName = "Notizen"
$form.Controls.Add($notes)

$status = New-Object System.Windows.Forms.Label
$status.Text = "Bereit"
$status.Left = 20; $status.Top = 330; $status.Width = 400
$status.Name = "labelStatus"
# no AccessibleName: WinForms would then report the fixed name instead of the label text
$form.Controls.Add($status)

$save = New-Object System.Windows.Forms.Button
$save.Text = "Speichern"
$save.Left = 470; $save.Top = 300; $save.Width = 120; $save.Height = 36
$save.Name = "buttonSave"
$save.AccessibleName = "Speichern"
$save.Add_Click({
    $content = "Name: " + $name.Text + "`r`nNotizen:`r`n" + $notes.Text + "`r`n"
    [System.IO.File]::WriteAllText($OutFile, $content, [System.Text.Encoding]::UTF8)
    $status.Text = "Gespeichert: " + $OutFile
})
$form.Controls.Add($save)

$clear = New-Object System.Windows.Forms.Button
$clear.Text = "Leeren"
$clear.Left = 340; $clear.Top = 300; $clear.Width = 120; $clear.Height = 36
$clear.Name = "buttonClear"
$clear.AccessibleName = "Leeren"
$clear.Add_Click({ $name.Text = ""; $notes.Text = ""; $status.Text = "Geleert" })
$form.Controls.Add($clear)

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = [Math]::Max(1000, $AutoCloseSeconds * 1000)
$timer.Add_Tick({ $form.Close() })
$timer.Start()

[void]$form.ShowDialog()
