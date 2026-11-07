@echo off
setlocal enabledelayedexpansion

set ROOT=%~dp0
set ROOT=%ROOT:~0,-1%
set IDEROOT=%ROOT%\..\..
set VC=%IDEROOT%\data\VC\VC2022\14.41.34120
set SDK=%IDEROOT%\data\VC\Windows Kits\10
set SDKV=10.0.19041.0
set OUT=%ROOT%\out
set CL_EXE=%VC%\bin\Hostx64\x64\cl.exe

if not exist "%CL_EXE%" (
  echo [ERR] cl.exe not found: %CL_EXE%
  exit /b 1
)
if not exist "%OUT%" mkdir "%OUT%"

pushd "%OUT%"
"%CL_EXE%" /nologo /std:c++17 /EHa /utf-8 /W2 /LD /MT /O2 ^
  /I"%ROOT%\src" ^
  /I"%VC%\include" ^
  /I"%SDK%\Include\%SDKV%\ucrt" ^
  /I"%SDK%\Include\%SDKV%\um" ^
  /I"%SDK%\Include\%SDKV%\shared" ^
  "%ROOT%\src\plugin.cpp" ^
  "%ROOT%\src\plug_abi.cpp" ^
  "%ROOT%\src\json.cpp" ^
  "%ROOT%\src\instance.cpp" ^
  "%ROOT%\src\http.cpp" ^
  "%ROOT%\src\mcp.cpp" ^
  "%ROOT%\src\tools.cpp" ^
  /link /DLL /OUT:"%OUT%\plug_xcmcp.dll" ^
  /LIBPATH:"%VC%\lib\x64" ^
  /LIBPATH:"%SDK%\Lib\%SDKV%\ucrt\x64" ^
  /LIBPATH:"%SDK%\Lib\%SDKV%\um\x64" ^
  "%ROOT%\lib\XCGUI.lib" ^
  kernel32.lib user32.lib ws2_32.lib
set RC=!ERRORLEVEL!
popd

if not "!RC!"=="0" (
  echo [ERR] build failed, rc=!RC!
  exit /b !RC!
)
echo [OK] %OUT%\plug_xcmcp.dll
