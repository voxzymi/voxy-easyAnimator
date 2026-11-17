# PyInstaller spec: builds a self-contained folder (no separate Python needed).
#   pyinstaller voxy_service.spec --noconfirm        ->  dist/voxy-service/voxy-service(.exe)
# For the NVIDIA fast mode, build in an environment that has onnxruntime-gpu instead of onnxruntime.
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

datas = [("voxy_service/assets", "voxy_service/assets")]
datas += collect_data_files("jsonschema")
datas += collect_data_files("jsonschema_specifications")
datas += collect_data_files("referencing")
binaries = collect_dynamic_libs("onnxruntime")
hidden = collect_submodules("voxy_service") + ["yaml", "cv2", "PIL.Image"]

a = Analysis(["run_service.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hidden,
             excludes=["tkinter", "matplotlib", "torch", "torchvision", "scipy", "pytest", "IPython", "onnx"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="voxy-service", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="voxy-service", upx=False)
