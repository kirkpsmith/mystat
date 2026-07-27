In this directory, create a virtual environment, activate it, and install the required packages:
```
python3 -m venv .venv
source .venv/bin/activate #.venv\Scripts\Activate.ps1 on Windows
python3 -m pip install -r requirements.txt #requirements-windows.txt on Windows
```


Run the program (sudo may be needed on Linux for USB privileges)
```
.venv/bin/python mystat.py #.venv/Scripts/python.exe on Windows
```

On Linux, the following commands are sometimes needed:

```
export LD_LIBRARY_PATH=$(python3 -c "import PyQt5.QtCore; print(PyQt5.QtCore.QLibraryInfo.location(PyQt5.QtCore.QLibraryInfo.LibrariesPath))"):$LD_LIBRARY_PATH
export QT_QPA_PLATFORM_PLUGIN_PATH=$(python3 -c "import PyQt5.QtCore; print(PyQt5.QtCore.QLibraryInfo.location(PyQt5.QtCore.QLibraryInfo.PluginsPath))")
```

To package the app, download [appimagetool](https://github.com/AppImage/appimagetool/releases) to this directory, rename to `appimagetool`, and run
```
./build-appimage.sh #build-exe.ps1 on Windows
 ```

To update requirements.txt:

```
python -m pip freeze > requirements.txt
```