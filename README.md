# FabLab 3D Print Bridge for Autodesk Fusion 360

Seamless, high-performance bridge connecting **Autodesk Fusion 360** directly to the **FabLab 3D Print Plate Builder** web application or offline slicing workflows.

---

## 🚀 Key Features

1. **Native Fusion 360 Selection UX**:
   - Styled after Fusion's native **Combine Tool** with an active cyan focus indicator and part counter (`[ ↖ | N selected ]`).
   - Full **solid body selection**: clicking any face, edge, or feature automatically resolves to its parent `BRepBody` and highlights the full body in both the 3D canvas and the Browser tree.
   - **Cumulative picking**: pick multiple bodies one by one without losing existing selections.
   - **Click-to-deselect**: clicking an already-selected body toggles it off.
   - **Deselect All (`✕`)** and **Select All Visible (`👁`)** shortcuts.

2. **Smart Destinations**:
   - **Send to FabLab Web App** *(Default)*: Spins up an ephemeral local HTTP bridge server, packages models and metadata, and automatically opens your browser with all parts placed on the 3D build plate.
   - **Export to Folder**: Saves high-refinement binary STLs locally, with an option to **"Keep Object Structure"** (preserves nested component subfolder hierarchies).

3. **Material & Configuration Profiles**:
   - Default material presets: PLA, PETG, ABS, TPU.
   - Configurable copies per part.
   - Automatic extraction of CAD appearance colors (RGB hex), bounding box dimensions, and part volumes.
   - High-refinement mesh tessellation via Fusion's native `ExportManager`.

4. **Streamlined UI**:
   - Lightweight, dark-themed responsive palette designed specifically for Fusion 360's embedded Chromium WebView.
   - Collapsible settings panel to keep the workspace clean and focused.

---

## 📦 Quick Installation

### Windows (Automated 1-Click)
1. Clone or download this repository.
2. Double-click `install_fusion_addon.bat`.
3. It copies the add-in files directly to:
   ```
   %APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\FabLabPrintBridge\
   ```

### Manual Installation (Windows or macOS)
Copy the entire repository folder contents to:

- **Windows**:
  ```
  %APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\FabLabPrintBridge\
  ```
  *(typically `C:\Users\<Username>\AppData\Roaming\Autodesk\Autodesk Fusion 360\API\AddIns\FabLabPrintBridge\`)*

- **macOS**:
  ```
  ~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/FabLabPrintBridge/
  ```

---

## 🛠️ Usage in Fusion 360

1. Open Autodesk Fusion 360.
2. Go to the **UTILITIES** tab (or press **Shift + S**).
3. Click **Scripts and Add-Ins**, then navigate to the **Add-Ins** tab.
4. Locate **FabLab 3D Print Bridge** in the list.
5. Click **Run** (check *"Run on Startup"* for automatic launch).
6. Click the **"Send to Plate Builder"** button in the Solid / Utilities toolbar.
7. Select the bodies you want to print, customize materials or destinations, and click **Send to FabLab Web App**!

---

## 🧪 Testing Standalone

You can test the ephemeral bridge server without launching Fusion 360:

```bash
python test_bridge_server.py
```

This launches a mock bridge server, serves two synthetic CAD objects with custom colors, and opens the Plate Builder web app to test the import pipeline.

---

## 📄 License
MIT License. Developed for the FabLab Ecosystem.
