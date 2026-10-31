; NetVision の Setup.exe。packaging/make-setup-windows.sh から makensis で組む（SRC、OUT、VERSION を渡す）。
; 利用者ごとの場所（%LOCALAPPDATA%\Programs）に入れるので、管理者の権限は要らない。
; 起動はスタートメニューとデスクトップのショートカットから。アプリのウィンドウで開き、ブラウザは使わない。
; データ（DB と資料）は %LOCALAPPDATA%\NetVision（前の版からなら %USERPROFILE%\.netvision）、AI のモデルは C:\LLM に置くので、入れた場所の外にある。
; 中身の Python が amd64 なので、Setup.exe も 64 ビットにそろえる
Target amd64-unicode
!include "MUI2.nsh"

Name "NetVision"
OutFile "${OUT}"
InstallDir "$LOCALAPPDATA\Programs\NetVision"
RequestExecutionLevel user
SetCompressor /SOLID lzma

; 黒い画面を出さないよう pythonw.exe で起動役を動かす。様子を見たいときは入れた場所の NetVision-console.bat
!define MUI_FINISHPAGE_RUN "$INSTDIR\python\pythonw.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS "-m scripts.launcher"
!define MUI_FINISHPAGE_RUN_TEXT "NetVision を起動する"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Japanese"
!insertmacro MUI_LANGUAGE "English"

!define UNINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\NetVision"

Section "Install"
  SetOutPath "$INSTDIR"
  File /r "${SRC}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  ; ショートカットの作業フォルダは、作る直前の SetOutPath になる。起動役は backend から動かす
  SetOutPath "$INSTDIR\backend"
  CreateDirectory "$SMPROGRAMS\NetVision"
  CreateShortcut "$SMPROGRAMS\NetVision\NetVision.lnk" "$INSTDIR\python\pythonw.exe" "-m scripts.launcher" "$INSTDIR\python\pythonw.exe"
  CreateShortcut "$SMPROGRAMS\NetVision\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\NetVision.lnk" "$INSTDIR\python\pythonw.exe" "-m scripts.launcher" "$INSTDIR\python\pythonw.exe"
  SetOutPath "$INSTDIR"
  WriteRegStr HKCU "${UNINST}" "DisplayName" "NetVision"
  WriteRegStr HKCU "${UNINST}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINST}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "${UNINST}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  ; 消すのは入れたもの（プログラム）だけ。データ（%LOCALAPPDATA%\NetVision）と AI のモデル（C:\LLM）は
  ; 入れた場所の外にあるので残る。消すと戻せないものには触らない
  RMDir /r "$INSTDIR"
  Delete "$SMPROGRAMS\NetVision\*.lnk"
  RMDir "$SMPROGRAMS\NetVision"
  Delete "$DESKTOP\NetVision.lnk"
  DeleteRegKey HKCU "${UNINST}"
  MessageBox MB_OK|MB_ICONINFORMATION "NetVision を消しました。$\r$\nデータ（$LOCALAPPDATA\NetVision）と AI のモデル（C:\LLM）は残してあります。" /SD IDOK
SectionEnd
