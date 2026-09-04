<#
.SYNOPSIS
  Sample prewritten script for the harness shell tool.
.DESCRIPTION
  Demonstrates invoking a governed PowerShell script with flags from a workflow
  step, e.g. "Run script hello.ps1 -Name World".
#>
param(
    [string]$Name = "world"
)

Write-Output "Hello, $Name — invoked from a governed prewritten script."
Write-Output "Working directory: $((Get-Location).Path)"
