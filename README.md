# MC Slicer

Desktop 3D fabrication slicer for turning STL, OBJ, 3MF, STEP, PLY, OFF, DAE, and DXF models into interlocking slice parts.

## Features

- PyQt5 desktop workflow with themed boot screen
- PyVista 3D preview
- Count, spacing, and sheet-thickness slicing
- Rectangular, rounded, dovetail, and T-slot notches
- Interlocking and welded assembly-step previews
- Physical-fit warnings for impossible spacing or missing crossing notches
- DXF and STEP/STL export paths

## Run

```powershell
pip install -r requirements.txt
python mc_slicer_gui.py
```

The bundled bulldog boot model and themed backgrounds are stored under `assets/`.
