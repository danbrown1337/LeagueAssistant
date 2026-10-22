# Builds dist/leagueassistant.exe (or dist/leagueassistant on Linux):
#     pyinstaller packaging/leagueassistant.spec
from PyInstaller.utils.hooks import collect_submodules

ROOT = SPECPATH + "/.."

a = Analysis(
    [ROOT + "/packaging/entry.py"],
    pathex=[ROOT],
    datas=[(ROOT + "/leagueassistant/web", "leagueassistant/web")],
    # uvicorn loads its protocol and loop classes by name.
    hiddenimports=collect_submodules("uvicorn"),
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name="leagueassistant",
    console=True,
    upx=False,
)
