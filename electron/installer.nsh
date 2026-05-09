; Kill any running Finn.exe (Electron shell + PyInstaller backend) before
; install or uninstall. Without this, the install/uninstall step can't replace
; or delete files that are still in use, and orphan processes survive past
; uninstall — visible to the user as "I uninstalled it but it's still running."
;
; /T walks the process tree so the spawned backend (running out of
; %TEMP%\_MEI...) goes down with the parent.

!macro customInit
  nsExec::Exec 'taskkill /F /IM "Finn.exe" /T'
  Sleep 1500
!macroend

!macro customUnInit
  nsExec::Exec 'taskkill /F /IM "Finn.exe" /T'
  Sleep 1500
!macroend
