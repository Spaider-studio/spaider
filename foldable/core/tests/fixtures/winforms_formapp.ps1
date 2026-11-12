# Second synthetic WinForms test application for the agent matrix (0.5):
# a small "Auftrag" form with a ComboBox, a CheckBox, two text fields, a status label and
# two buttons. "Anlegen" writes the values to -OutFile; "Zurücksetzen" clears the form.
# Optional -Variant 2 renames the buttons (simulates an application update for repair tests).
param(
    [string]$Title = "SpAIder Auftragsformular",
    [string]$OutFile = "",
    [int]$Variant = 1,
    [int]$AutoCloseSeconds = 600
)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object System.Windows.Forms.Form
$form.Text = $Title
$form.Size = New-Object System.Drawing.Size(520, 400)
$form.StartPosition = "CenterScreen"
$form.TopMost = $false

$l1 = New-Object System.Windows.Forms.Label; $l1.Text = "Kunde:"; $l1.Location = New-Object System.Drawing.Point(20, 20); $l1.AutoSize = $true; $form.Controls.Add($l1)
$kunde = New-Object System.Windows.Forms.TextBox; $kunde.Name = "textKunde"; $kunde.AccessibleName = "Kunde"; $kunde.Location = New-Object System.Drawing.Point(140, 16); $kunde.Size = New-Object System.Drawing.Size(340, 24); $form.Controls.Add($kunde)

$l2 = New-Object System.Windows.Forms.Label; $l2.Text = "Menge:"; $l2.Location = New-Object System.Drawing.Point(20, 60); $l2.AutoSize = $true; $form.Controls.Add($l2)
$menge = New-Object System.Windows.Forms.TextBox; $menge.Name = "textMenge"; $menge.AccessibleName = "Menge"; $menge.Location = New-Object System.Drawing.Point(140, 56); $menge.Size = New-Object System.Drawing.Size(120, 24); $form.Controls.Add($menge)

$l3 = New-Object System.Windows.Forms.Label; $l3.Text = "Priorität:"; $l3.Location = New-Object System.Drawing.Point(20, 100); $l3.AutoSize = $true; $form.Controls.Add($l3)
$prio = New-Object System.Windows.Forms.ComboBox; $prio.Name = "comboPrio"; $prio.AccessibleName = "Priorität"; $prio.DropDownStyle = "DropDownList"
[void]$prio.Items.AddRange(@("niedrig", "mittel", "hoch")); $prio.SelectedIndex = 0
$prio.Location = New-Object System.Drawing.Point(140, 96); $prio.Size = New-Object System.Drawing.Size(160, 24); $form.Controls.Add($prio)

$eil = New-Object System.Windows.Forms.CheckBox; $eil.Name = "checkEil"; $eil.AccessibleName = "Eilauftrag"; $eil.Text = "Eilauftrag"; $eil.Location = New-Object System.Drawing.Point(140, 136); $eil.AutoSize = $true; $form.Controls.Add($eil)

$l4 = New-Object System.Windows.Forms.Label; $l4.Text = "Bemerkung:"; $l4.Location = New-Object System.Drawing.Point(20, 176); $l4.AutoSize = $true; $form.Controls.Add($l4)
$bem = New-Object System.Windows.Forms.TextBox; $bem.Name = "textBemerkung"; $bem.AccessibleName = "Bemerkung"; $bem.Multiline = $true; $bem.Location = New-Object System.Drawing.Point(140, 172); $bem.Size = New-Object System.Drawing.Size(340, 70); $form.Controls.Add($bem)

$status = New-Object System.Windows.Forms.Label; $status.Name = "labelStatus"; $status.AccessibleName = "Status"; $status.Text = "Bereit"; $status.Location = New-Object System.Drawing.Point(20, 300); $status.AutoSize = $true; $form.Controls.Add($status)

$anlegen = New-Object System.Windows.Forms.Button; $anlegen.Name = "buttonAnlegen"
if ($Variant -eq 2) { $anlegen.Text = "Auftrag speichern"; $anlegen.AccessibleName = "Auftrag speichern" } else { $anlegen.Text = "Anlegen"; $anlegen.AccessibleName = "Anlegen" }
$anlegen.Location = New-Object System.Drawing.Point(140, 256); $anlegen.Size = New-Object System.Drawing.Size(140, 30)
$anlegen.Add_Click({
    if ($OutFile -ne "") {
        $lines = @("Kunde: " + $kunde.Text, "Menge: " + $menge.Text, "Prioritaet: " + $prio.SelectedItem, "Eilauftrag: " + $eil.Checked, "Bemerkung: " + $bem.Text)
        [System.IO.File]::WriteAllLines($OutFile, $lines, (New-Object System.Text.UTF8Encoding($false)))
    }
    $status.Text = "Angelegt: " + $kunde.Text + " (" + $menge.Text + ")"
})
$form.Controls.Add($anlegen)

$reset = New-Object System.Windows.Forms.Button; $reset.Name = "buttonReset"
if ($Variant -eq 2) { $reset.Text = "Formular leeren"; $reset.AccessibleName = "Formular leeren" } else { $reset.Text = "Zurücksetzen"; $reset.AccessibleName = "Zurücksetzen" }
$reset.Location = New-Object System.Drawing.Point(300, 256); $reset.Size = New-Object System.Drawing.Size(140, 30)
$reset.Add_Click({ $kunde.Text = ""; $menge.Text = ""; $prio.SelectedIndex = 0; $eil.Checked = $false; $bem.Text = ""; $status.Text = "Zurückgesetzt" })
$form.Controls.Add($reset)

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = [Math]::Max(1000, $AutoCloseSeconds * 1000)
$timer.Add_Tick({ $form.Close() })
$timer.Start()
[void]$form.ShowDialog()
