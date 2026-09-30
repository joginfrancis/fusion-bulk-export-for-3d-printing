# Author: FabLab Team
# Description: Modern 3D Print Builder Dialog & Multi-destination Export Bridge for Autodesk Fusion 360.
# Features: Open in Bambu Studio, Send to FabLab Web App, Save to Local Folder, Live Viewport Selection, Per-object Qty & Material.

import os
import sys
import json
import uuid
import time
import socket
import threading
import webbrowser
import tempfile
import shutil
import urllib.parse
import traceback
import subprocess
import datetime
import getpass
from http.server import SimpleHTTPRequestHandler
from socketserver import ThreadingTCPServer

try:
    import adsk.core
    import adsk.fusion
    IN_FUSION = True
except ImportError:
    IN_FUSION = False

# Global state references to prevent Python GC from dropping objects
_app = None
_ui = None
_handlers = []
_palette = None
_bridge_server = None
_bridge_thread = None
_bridge_port = 0
_bridge_dir = None

# Active selected bodies reference cache
_cached_bodies = {}

# Identifiers
CMD_ID = 'FabLabSendForPrintingCmd_v9'
CMD_NAME = 'Send for 3D Printing'
CMD_DESCRIPTION = 'Open 3D Print Builder to export models to Bambu Studio, FabLab Web App, or Local Folder'

CUSTOM_PANEL_ID = 'FabLabToolbarPanel'
CUSTOM_PANEL_NAME = 'FABLAB'

PALETTE_ID = 'FabLab3DPrintBuilderPalette'
PALETTE_NAME = '3D PRINT BUILDER'
PALETTE_URL = 'resources/dialog.html'

OLD_CMD_IDS = [
    'FabLabSendToPlateBuilderCmd',
    'FabLab3DPrintBuilderCmd',
    'FabLab3DPrintBuilderCmd_v2',
    'FabLab3DPrintBuilderCmd_v3',
    'FabLab3DPrintBuilderCmd_v4',
    'FabLab3DPrintBuilderCmd_v5',
    'FabLab3DPrintBuilderCmd_v6',
    'FabLab3DPrintBuilderCmd_v7',
    'FabLabSendForPrintingCmd_v8',
    'FabLabSendForPrintingCmd_v9',
    'FabLabToolsDropdown',
    'FabLabToolsDropdown_v7'
]

# ── Private Network Access & CORS HTTP Handler ──────────────────────────────
class BridgeHTTPRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Requested-With')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        if self.path in ('/done', '/done/'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
            def delayed_stop():
                time.sleep(1.5)
                stop_bridge_server()
            threading.Thread(target=delayed_stop, daemon=True).start()
            return
        super().do_GET()

    def log_message(self, format, *args):
        pass

class ReusableThreadingTCPServer(ThreadingTCPServer):
    allow_reuse_address = True

def find_free_port(start_port=8001, max_port=8050):
    for p in range(start_port, max_port):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', p))
                return p
        except OSError:
            continue
    return 0

def start_bridge_server(serve_directory):
    global _bridge_server, _bridge_thread, _bridge_port, _bridge_dir
    stop_bridge_server()
    _bridge_dir = serve_directory
    port = find_free_port(8001, 8050)
    if port == 0:
        return None

    def handler_factory(*args, **kwargs):
        return BridgeHTTPRequestHandler(*args, directory=serve_directory, **kwargs)

    try:
        server = ReusableThreadingTCPServer(('127.0.0.1', port), handler_factory)
        _bridge_server = server
        _bridge_port = port
        _bridge_thread = threading.Thread(target=server.serve_forever, daemon=True)
        _bridge_thread.start()

        def timeout_shutdown():
            time.sleep(600)
            stop_bridge_server()
        threading.Thread(target=timeout_shutdown, daemon=True).start()
        return port
    except Exception:
        return None

def stop_bridge_server():
    global _bridge_server, _bridge_thread, _bridge_port
    if _bridge_server:
        try:
            _bridge_server.shutdown()
            _bridge_server.server_close()
        except Exception:
            pass
        _bridge_server = None
    _bridge_thread = None
    _bridge_port = 0

def find_bambu_studio_path():
    candidates = [
        r'C:\Program Files\Bambu Studio\bambu-studio.exe',
        r'C:\Program Files (x86)\Bambu Studio\bambu-studio.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Programs\BambuStudio\bambu-studio.exe'),
        os.path.expandvars(r'%LOCALAPPDATA%\BambuStudio\bambu-studio.exe'),
        os.path.expandvars(r'%PROGRAMFILES%\OrcaSlicer\orca-slicer.exe'),
        os.path.expandvars(r'%PROGRAMFILES%\Prusa3D\PrusaSlicer\prusa-slicer.exe')
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return r'C:\Program Files\Bambu Studio\bambu-studio.exe'

def hex_to_friendly_color_name(hex_code):
    try:
        r = int(hex_code[1:3], 16)
        g = int(hex_code[3:5], 16)
        b = int(hex_code[5:7], 16)
    except Exception:
        return 'Grey'

    STANDARD_COLORS = [
        ('White', 245, 245, 245),
        ('Black', 25, 25, 25),
        ('Grey', 128, 128, 128),
        ('Red', 220, 35, 35),
        ('Orange', 245, 125, 20),
        ('Yellow', 240, 210, 30),
        ('Green', 40, 180, 60),
        ('Blue', 30, 125, 230),
        ('Purple', 140, 50, 190),
        ('Pink', 235, 110, 165),
        ('Cyan', 30, 200, 220),
        ('Brown', 130, 80, 40)
    ]
    best_name = 'Grey'
    best_dist = float('inf')
    for name, pr, pg, pb in STANDARD_COLORS:
        dist = (r - pr)**2 + (g - pg)**2 + (b - pb)**2
        if dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name

def extract_body_appearance(body):
    color_hex = '#5d8588'
    appearance_name = ''
    try:
        app = body.appearance
        if not app and hasattr(body, 'parentComponent') and body.parentComponent:
            app = body.parentComponent.appearance
        if not app and hasattr(body, 'material') and body.material:
            app = body.material.appearance

        if app:
            appearance_name = app.name
            props = app.appearanceProperties
            for i in range(props.count):
                prop = props.item(i)
                if hasattr(prop, 'value') and hasattr(prop.value, 'red') and hasattr(prop.value, 'green') and hasattr(prop.value, 'blue'):
                    c = prop.value
                    if c.red != 0 or c.green != 0 or c.blue != 0:
                        color_hex = f'#{c.red:02x}{c.green:02x}{c.blue:02x}'
                        break
    except Exception:
        pass

    color_name = hex_to_friendly_color_name(color_hex)
    return color_hex, color_name, appearance_name

def detect_body_shape(body):
    try:
        faces = body.faces
        cyl_count = 0
        plane_count = 0
        nurbs_count = 0
        tri_face_count = 0

        for i in range(faces.count):
            f = faces.item(i)
            geom = f.geometry
            st = getattr(geom, 'surfaceType', -1)
            # SurfaceTypes: PlaneSurfaceType=0, CylinderSurfaceType=1, ConeSurfaceType=2, SphereSurfaceType=3, TorusSurfaceType=4, NurbsSurfaceType=7
            if st == 1 or st == 5:
                cyl_count += 1
            elif st == 0:
                plane_count += 1
                if hasattr(f, 'edges') and f.edges.count == 3:
                    tri_face_count += 1
            elif st == 7:
                nurbs_count += 1

        if cyl_count > 0:
            return 'cylinder'
        if tri_face_count > 0:
            return 'triangle'
        if nurbs_count > 0:
            return 'organic'
        if plane_count == 6:
            return 'cube'
        if plane_count > 0:
            return 'box'
    except Exception:
        pass
def get_body_component_path(body):
    """
    Returns the hierarchy list of occurrence/component names,
    e.g. ['Assembly', 'SubAssembly_1']
    """
    path = []
    try:
        occ = getattr(body, 'assemblyContext', None)
        while occ:
            raw_name = occ.name
            clean_name = ''.join(c if c.isalnum() or c in ('_', '-', ' ') else '_' for c in raw_name).strip()
            if clean_name:
                path.insert(0, clean_name.replace(':', '_'))
            occ = getattr(occ, 'parentOccurrence', None)

        if not path and hasattr(body, 'parentComponent') and body.parentComponent:
            p_comp = body.parentComponent
            root = getattr(getattr(body, 'parentComponent', None), 'parentDesign', None)
            root_comp = root.rootComponent if root else None
            if p_comp != root_comp:
                clean_name = ''.join(c if c.isalnum() or c in ('_', '-', ' ') else '_' for c in p_comp.name).strip()
                if clean_name:
                    path.append(clean_name.replace(':', '_'))
    except Exception:
        pass
    return path

def collect_all_visible_bodies(design):
    bodies = []
    if not design:
        return bodies
    root = design.rootComponent
    for b in root.bRepBodies:
        if b.isVisible:
            bodies.append(b)
    for occ in root.allOccurrences:
        if occ.isVisible:
            try:
                for b in occ.bRepBodies:
                    if b.isVisible:
                        bodies.append(b)
            except Exception:
                pass
    unique = []
    seen = set()
    for b in bodies:
        bid = getattr(b, 'entityToken', getattr(b, 'name', id(b)))
        if bid not in seen:
            seen.add(bid)
            unique.append(b)
    return unique

def serialize_bodies(body_list):
    global _cached_bodies
    _cached_bodies.clear()
    serialized = []
    for idx, body in enumerate(body_list):
        try:
            token = getattr(body, 'entityToken', str(idx))
            _cached_bodies[token] = body
            _cached_bodies[body.name] = body

            bbox = body.boundingBox
            w = round((bbox.maxPoint.x - bbox.minPoint.x) * 10.0, 1)
            d = round((bbox.maxPoint.y - bbox.minPoint.y) * 10.0, 1)
            h = round((bbox.maxPoint.z - bbox.minPoint.z) * 10.0, 1)

            shape = detect_body_shape(body)
            color_hex, color_name, app_name = extract_body_appearance(body)

            serialized.append({
                'id': str(idx + 1),
                'name': body.name,
                'token': token,
                'material': 'PLA',
                'colorName': color_name,
                'colorHex': color_hex,
                'appearanceName': app_name,
                'quantity': 1,
                'dimensions': {'width': w, 'depth': d, 'height': h},
                'shape': shape
            })
        except Exception:
            pass
    return serialized

def resolve_all_bodies_from_selection(selections):
    bodies = []
    if not selections or selections.count == 0:
        return bodies
    for i in range(selections.count):
        ent = selections.item(i).entity
        if isinstance(ent, adsk.fusion.BRepBody):
            if ent.isVisible:
                bodies.append(ent)
        elif hasattr(ent, 'body') and ent.body and ent.body.isVisible:
            # Resolves BRepFace, BRepEdge, BRepVertex clicked in 3D canvas!
            bodies.append(ent.body)
        elif hasattr(ent, 'bRepBodies'):
            try:
                for b in ent.bRepBodies:
                    if b.isVisible:
                        bodies.append(b)
            except Exception:
                pass
    unique = []
    seen = set()
    for b in bodies:
        bid = getattr(b, 'entityToken', getattr(b, 'name', id(b)))
        if bid not in seen:
            seen.add(bid)
            unique.append(b)
    return unique

def is_valid_body(b):
    try:
        return b is not None and getattr(b, 'isValid', False)
    except Exception:
        return False

def get_body_identifier(body):
    if not body:
        return ''
    try:
        tok = getattr(body, 'entityToken', None)
        if tok:
            return tok
    except Exception:
        pass
    try:
        if hasattr(body, 'name') and body.name:
            pname = ''
            if hasattr(body, 'parentComponent') and body.parentComponent:
                pname = getattr(body.parentComponent, 'name', '') + '_'
            return pname + body.name
    except Exception:
        pass
    return str(id(body))

def is_same_body(b1, b2):
    if b1 is None or b2 is None:
        return False
    if b1 == b2:
        return True
    try:
        id1 = get_body_identifier(b1)
        id2 = get_body_identifier(b2)
        if id1 and id2 and id1 == id2:
            return True
    except Exception:
        pass
    return False

def get_current_design_info():
    if not IN_FUSION or not _app:
        return {'designName': 'Fusion_Design', 'bodies': [], 'bambuPath': find_bambu_studio_path(), 'defaultFolder': os.path.expanduser('~/Documents')}

    design = _app.activeProduct
    design_name = 'Fusion_Design'
    if design and design.rootComponent and design.rootComponent.name:
        design_name = design.rootComponent.name
        if ' v' in design_name:
            design_name = design_name.rsplit(' v', 1)[0]

    # Collect bodies: active selections if any, otherwise all visible bodies
    selected_bodies = []
    if _ui and _ui.activeSelections and _ui.activeSelections.count > 0:
        selected_bodies = resolve_all_bodies_from_selection(_ui.activeSelections)

    if not selected_bodies and design:
        selected_bodies = collect_all_visible_bodies(design)

    return {
        'designName': design_name,
        'bodies': serialize_bodies(selected_bodies),
        'bambuPath': find_bambu_studio_path(),
        'defaultFolder': os.path.expanduser('~/Documents')
    }

# ── Selection Watcher Event Handler ──────────────────────────────────────────
if IN_FUSION:
    class ActiveSelectionChangedHandler(adsk.core.ActiveSelectionEventHandler):
        def __init__(self, palette):
            super().__init__()
            self._palette = palette
            self.suppress = False
            self.selected_bodies = []

        def notify(self, args):
            try:
                if self.suppress or not self._palette or not self._palette.isVisible:
                    return

                # If activeSelections is cleared (e.g. user clicked empty space to orbit/pan),
                # preserve the current selected bodies in CAD!
                if not _ui or not _ui.activeSelections or _ui.activeSelections.count == 0:
                    if self.selected_bodies:
                        self.suppress = True
                        try:
                            for b in self.selected_bodies:
                                try:
                                    _ui.activeSelections.add(b)
                                except Exception:
                                    pass
                        finally:
                            self.suppress = False
                    return

                # Resolve clicked entity (BRepFace, BRepEdge, etc.) to full solid BRepBody
                clicked_bodies = resolve_all_bodies_from_selection(_ui.activeSelections)
                if not clicked_bodies:
                    return

                # Clean any invalidated bodies
                self.selected_bodies = [b for b in self.selected_bodies if is_valid_body(b)]

                # Check if user clicked a single body that is already in selection -> Toggle OFF (Deselect)
                if len(clicked_bodies) == 1:
                    cb = clicked_bodies[0]
                    already_idx = -1
                    for idx, sb in enumerate(self.selected_bodies):
                        if is_same_body(sb, cb):
                            already_idx = idx
                            break

                    if already_idx >= 0:
                        # Body already in selection: clicking again removes it!
                        self.selected_bodies.pop(already_idx)
                    else:
                        # New body: keep all existing ones and add this one!
                        self.selected_bodies.append(cb)
                else:
                    # Multiple bodies selected (e.g. window/box select or occurrence)
                    for cb in clicked_bodies:
                        if not any(is_same_body(sb, cb) for sb in self.selected_bodies):
                            self.selected_bodies.append(cb)

                # Update _ui.activeSelections with the solid BRepBodies (NOT faces!)
                # This ensures the entire 3D solid body turns blue in the canvas!
                self.suppress = True
                try:
                    _ui.activeSelections.clear()
                    for sb in self.selected_bodies:
                        try:
                            _ui.activeSelections.add(sb)
                        except Exception:
                            pass
                    if _app and _app.activeViewport:
                        _app.activeViewport.refresh()
                finally:
                    self.suppress = False

                # Serialize and push syncSelection to HTML table & Combine count pill
                serialized = serialize_bodies(self.selected_bodies)
                tokens = [b.get('token') for b in serialized]
                names = [b.get('name') for b in serialized]
                self._palette.sendInfoToHTML('syncSelection', json.dumps({
                    'bodies': serialized,
                    'tokens': tokens,
                    'names': names
                }))
            except Exception:
                pass

    # ── Palette HTML Incoming Message Handler ────────────────────────────────
    class PaletteHTMLEventHandler(adsk.core.HTMLEventHandler):
        def __init__(self, palette, watcher=None):
            super().__init__()
            self._palette = palette
            self._watcher = watcher

        def notify(self, args):
            global _cached_bodies
            try:
                html_args = adsk.core.HTMLEventArgs.cast(args)
                action = html_args.action
                data_str = html_args.data or '{}'

                try:
                    payload = json.loads(data_str) if isinstance(data_str, str) and data_str.startswith('{') else {}
                except Exception:
                    payload = {}

                # 1. Ready Handshake
                if action == 'ready':
                    info = get_current_design_info()
                    if self._watcher and self._watcher.selected_bodies:
                        info['bodies'] = serialize_bodies(self._watcher.selected_bodies)
                    self._palette.sendInfoToHTML('init', json.dumps(info))

                # 2. Highlight Body in 3D Canvas and Browser Tree
                elif action == 'highlightBody':
                    token = payload.get('token')
                    name = payload.get('name')
                    body = _cached_bodies.get(token) or _cached_bodies.get(name)
                    if not body and _app and _app.activeProduct:
                        for b in collect_all_visible_bodies(_app.activeProduct):
                            if b.name == name:
                                body = b
                                break

                    if body and _ui:
                        if self._watcher:
                            self._watcher.suppress = True
                        try:
                            _ui.activeSelections.clear()
                            if self._watcher and self._watcher.selected_bodies:
                                for sb in self._watcher.selected_bodies:
                                    try:
                                        _ui.activeSelections.add(sb)
                                    except Exception:
                                        pass
                            else:
                                _ui.activeSelections.add(body)
                            vp = _app.activeViewport
                            if vp:
                                vp.refresh()
                        finally:
                            if self._watcher:
                                self._watcher.suppress = False

                # 3. Select All Visible Bodies
                elif action == 'selectAllVisible':
                    if _app and _app.activeProduct:
                        all_vis = collect_all_visible_bodies(_app.activeProduct)
                        if self._watcher:
                            self._watcher.selected_bodies = list(all_vis)
                        serialized = serialize_bodies(all_vis)
                        tokens = [b.get('token') for b in serialized]
                        names = [b.get('name') for b in serialized]

                        if _ui:
                            if self._watcher:
                                self._watcher.suppress = True
                            try:
                                _ui.activeSelections.clear()
                                for b in all_vis:
                                    try:
                                        _ui.activeSelections.add(b)
                                    except Exception:
                                        pass
                                if _app.activeViewport:
                                    _app.activeViewport.refresh()
                            finally:
                                if self._watcher:
                                    self._watcher.suppress = False

                        self._palette.sendInfoToHTML('syncSelection', json.dumps({
                            'bodies': serialized,
                            'tokens': tokens,
                            'names': names
                        }))

                # 4. Remove Single Body (Deselect / Remove from table)
                elif action == 'removeBody':
                    token = payload.get('token')
                    name = payload.get('name')
                    body = _cached_bodies.get(token) or _cached_bodies.get(name)
                    if not body and _app and _app.activeProduct:
                        for b in collect_all_visible_bodies(_app.activeProduct):
                            if b.name == name:
                                body = b
                                break

                    if token in _cached_bodies:
                        try:
                            del _cached_bodies[token]
                        except Exception:
                            pass
                    if name in _cached_bodies:
                        try:
                            del _cached_bodies[name]
                        except Exception:
                            pass

                    if self._watcher and self._watcher.selected_bodies:
                        self._watcher.selected_bodies = [
                            b for b in self._watcher.selected_bodies
                            if not is_same_body(b, body) and getattr(b, 'name', None) != name
                        ]

                    if _ui and _ui.activeSelections:
                        if self._watcher:
                            self._watcher.suppress = True
                        try:
                            remaining = []
                            for idx in range(_ui.activeSelections.count):
                                try:
                                    sel_ent = _ui.activeSelections.item(idx).entity
                                    is_match = False
                                    if body and sel_ent == body:
                                        is_match = True
                                    elif body and hasattr(sel_ent, 'body') and sel_ent.body == body:
                                        is_match = True
                                    elif getattr(sel_ent, 'name', None) == name:
                                        is_match = True
                                    elif hasattr(sel_ent, 'body') and getattr(sel_ent.body, 'name', None) == name:
                                        is_match = True

                                    if not is_match:
                                        remaining.append(sel_ent)
                                except Exception:
                                    pass

                            _ui.activeSelections.clear()
                            for r in remaining:
                                try:
                                    _ui.activeSelections.add(r)
                                except Exception:
                                    pass

                            if _app and _app.activeViewport:
                                _app.activeViewport.refresh()
                        finally:
                            if self._watcher:
                                self._watcher.suppress = False

                # 5. Clear Selection
                elif action == 'clearSelection':
                    _cached_bodies.clear()
                    if self._watcher:
                        self._watcher.selected_bodies = []
                    if _ui and _ui.activeSelections:
                        if self._watcher:
                            self._watcher.suppress = True
                        try:
                            _ui.activeSelections.clear()
                            if _app and _app.activeViewport:
                                _app.activeViewport.refresh()
                        finally:
                            if self._watcher:
                                self._watcher.suppress = False

                # 5. Browse Destination Folder
                elif action == 'browseFolder':
                    if _ui:
                        folder_dlg = _ui.createFolderDialog()
                        folder_dlg.title = 'Select Destination Folder'
                        curr = payload.get('current', '')
                        if curr and os.path.exists(curr):
                            folder_dlg.initialDirectory = curr
                        else:
                            folder_dlg.initialDirectory = os.path.expanduser('~/Documents')

                        if folder_dlg.showDialog() == adsk.core.DialogResults.DialogOK:
                            chosen = folder_dlg.folder
                            if chosen:
                                self._palette.sendInfoToHTML('folderChosen', json.dumps({'path': chosen}))

                # 6. Browse Bambu Executable
                elif action == 'browseFile':
                    if _ui:
                        file_dlg = _ui.createFileDialog()
                        file_dlg.title = 'Locate Bambu Studio Executable'
                        file_dlg.filter = 'Executable (*.exe);;All Files (*.*)'
                        curr = payload.get('current', '')
                        if curr and os.path.exists(curr):
                            file_dlg.initialDirectory = os.path.dirname(curr)
                        else:
                            file_dlg.initialDirectory = r'C:\Program Files\Bambu Studio'

                        if file_dlg.showDialogOpen() == adsk.core.DialogResults.DialogOK:
                            chosen = file_dlg.filename
                            if chosen:
                                self._palette.sendInfoToHTML('fileChosen', json.dumps({'path': chosen}))

                # 7. Cancel / Close Dialog
                elif action == 'cancel':
                    if self._palette:
                        self._palette.isVisible = False

                # 8. Export Execution
                elif action == 'export':
                    self.execute_export(payload)

            except Exception:
                if _ui:
                    _ui.messageBox(f'Error processing UI message:\n{traceback.format_exc()}', '3D Print Builder')

        def execute_export(self, payload):
            global _cached_bodies
            try:
                dest = payload.get('destination', 'webapp')
                bodies_meta = payload.get('bodies', [])
                auto_close = payload.get('autoClose', True)

                design = _app.activeProduct
                if not design:
                    if _ui:
                        _ui.messageBox('No active Fusion 360 design found.', '3D Print Builder')
                    return

                design_name = design.rootComponent.name or 'Fusion_Design'
                if ' v' in design_name:
                    design_name = design_name.rsplit(' v', 1)[0]

                export_mgr = design.exportManager

                # Resolve actual BRepBody objects
                resolved_bodies = []
                all_design_bodies = collect_all_visible_bodies(design)

                for item in bodies_meta:
                    token = item.get('token')
                    name = item.get('name')
                    body = _cached_bodies.get(token) or _cached_bodies.get(name)
                    if not body:
                        for b in all_design_bodies:
                            if b.name == name:
                                body = b
                                break
                    if body:
                        resolved_bodies.append({
                            'body': body,
                            'name': body.name,
                            'material': item.get('material', 'PLA'),
                            'quantity': max(1, int(item.get('quantity', 1)))
                        })

                if not resolved_bodies:
                    if _ui:
                        _ui.messageBox('No valid solid bodies found to export.', '3D Print Builder')
                    return

                # ══════════════════════════════════════════════════════════════
                # 1. OPEN IN BAMBU STUDIO
                # ══════════════════════════════════════════════════════════════
                if dest == 'bambu':
                    bambu_exe = payload.get('bambuPath') or find_bambu_studio_path()
                    session_id = str(uuid.uuid4())[:8]
                    temp_dir = os.path.join(tempfile.gettempdir(), 'fablab_bridge', f'bambu_{session_id}')
                    os.makedirs(temp_dir, exist_ok=True)

                    stl_paths = []
                    for idx, item in enumerate(resolved_bodies):
                        body = item['body']
                        sname = ''.join(c if c.isalnum() or c in ('_', '-') else '_' for c in body.name)
                        stl_filename = f'{sname}.stl'
                        stl_filepath = os.path.join(temp_dir, stl_filename)

                        stl_opts = export_mgr.createSTLExportOptions(body, stl_filepath)
                        stl_opts.sendToPrintUtility = False
                        stl_opts.isBinaryFormat = True
                        stl_opts.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
                        export_mgr.execute(stl_opts)
                        stl_paths.append(stl_filepath)

                    if os.path.exists(bambu_exe):
                        subprocess.Popen([bambu_exe, *stl_paths])
                    else:
                        for sp in stl_paths:
                            try:
                                os.startfile(sp)
                            except Exception:
                                pass

                    if auto_close and self._palette:
                        self._palette.isVisible = False

                # ══════════════════════════════════════════════════════════════
                # 2. SAVE TO LOCAL FOLDER
                # ══════════════════════════════════════════════════════════════
                elif dest == 'folder':
                    fname = payload.get('folderName') or f'{design_name}_STLs'
                    fname = ''.join(c if c.isalnum() or c in ('_', '-', ' ') else '_' for c in fname)
                    root_dir = payload.get('rootFolder') or os.path.expanduser('~/Documents')
                    out_dir = os.path.normpath(os.path.join(root_dir, fname))
                    os.makedirs(out_dir, exist_ok=True)
                    preserve_structure = payload.get('preserveStructure', False)

                    bom_lines = [
                        "===========================================================",
                        "       FABLAB 3D PRINT MANAGEMENT - BILL OF MATERIALS      ",
                        "===========================================================",
                        f"Design Name:  {design_name}",
                        f"Export Date:  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        f"Target Dir:   {out_dir}",
                        f"Structure:    {'Hierarchical Components' if preserve_structure else 'Flat Folder'}",
                        "-----------------------------------------------------------",
                        f"{'Part / Hierarchy':<36} | {'Qty':<5} | {'Material':<8} | {'Dimensions (mm)':<18}",
                        "-----------------------------------------------------------"
                    ]

                    manifest_parts = []
                    for idx, item in enumerate(resolved_bodies):
                        body = item['body']
                        sname = ''.join(c if c.isalnum() or c in ('_', '-') else '_' for c in body.name)
                        stl_filename = f'{sname}.stl'

                        target_dir = out_dir
                        rel_path_str = ''
                        if preserve_structure:
                            comp_hierarchy = get_body_component_path(body)
                            if comp_hierarchy:
                                target_dir = os.path.join(out_dir, *comp_hierarchy)
                                os.makedirs(target_dir, exist_ok=True)
                                rel_path_str = '/'.join(comp_hierarchy)

                        stl_filepath = os.path.join(target_dir, stl_filename)

                        stl_opts = export_mgr.createSTLExportOptions(body, stl_filepath)
                        stl_opts.sendToPrintUtility = False
                        stl_opts.isBinaryFormat = True
                        stl_opts.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
                        export_mgr.execute(stl_opts)

                        bbox = body.boundingBox
                        w = round((bbox.maxPoint.x - bbox.minPoint.x) * 10.0, 1)
                        d = round((bbox.maxPoint.y - bbox.minPoint.y) * 10.0, 1)
                        h = round((bbox.maxPoint.z - bbox.minPoint.z) * 10.0, 1)
                        dim_str = f'{w} × {d} × {h}'

                        display_name = f"{rel_path_str}/{sname}" if rel_path_str else sname
                        bom_lines.append(f"{display_name:<36} | {item['quantity']:<5} | {item['material']:<8} | {dim_str:<18}")
                        manifest_parts.append({
                            'id': f'part_{idx+1}',
                            'name': stl_filename,
                            'fileName': stl_filename,
                            'relativePath': os.path.relpath(stl_filepath, out_dir).replace('\\', '/'),
                            'componentHierarchy': rel_path_str,
                            'bodyName': body.name,
                            'material': item['material'],
                            'quantity': item['quantity'],
                            'dimensions': {'width': w, 'depth': d, 'height': h}
                        })

                    bom_lines.append("-----------------------------------------------------------")
                    bom_lines.append(f"Total Unique Models: {len(resolved_bodies)}")
                    bom_lines.append(f"Total Parts to Print: {sum(p['quantity'] for p in manifest_parts)}")
                    bom_lines.append("===========================================================")

                    with open(os.path.join(out_dir, 'bill_of_materials.txt'), 'w', encoding='utf-8') as f:
                        f.write('\n'.join(bom_lines))

                    with open(os.path.join(out_dir, 'manifest.json'), 'w', encoding='utf-8') as f:
                        json.dump({
                            'designName': design_name,
                            'exportedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                            'parts': manifest_parts
                        }, f, indent=2)

                    try:
                        os.startfile(out_dir)
                    except Exception:
                        subprocess.Popen(['explorer.exe', out_dir])

                    if auto_close and self._palette:
                        self._palette.isVisible = False

                # ══════════════════════════════════════════════════════════════
                # 3. SEND TO FABLAB WEB APP
                # ══════════════════════════════════════════════════════════════
                elif dest == 'webapp':
                    session_id = str(uuid.uuid4())[:8]
                    temp_dir = os.path.join(tempfile.gettempdir(), 'fablab_bridge', f'session_{session_id}')
                    os.makedirs(temp_dir, exist_ok=True)

                    manifest_parts = []
                    for idx, item in enumerate(resolved_bodies):
                        body = item['body']
                        sname = ''.join(c if c.isalnum() or c in ('_', '-') else '_' for c in body.name)
                        stl_filename = f'{sname}.stl'
                        stl_filepath = os.path.join(temp_dir, stl_filename)

                        stl_opts = export_mgr.createSTLExportOptions(body, stl_filepath)
                        stl_opts.sendToPrintUtility = False
                        stl_opts.isBinaryFormat = True
                        stl_opts.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
                        export_mgr.execute(stl_opts)

                        bbox = body.boundingBox
                        w = round((bbox.maxPoint.x - bbox.minPoint.x) * 10.0, 2)
                        d = round((bbox.maxPoint.y - bbox.minPoint.y) * 10.0, 2)
                        h = round((bbox.maxPoint.z - bbox.minPoint.z) * 10.0, 2)

                        manifest_parts.append({
                            'id': f'part_{idx+1}',
                            'name': stl_filename,
                            'fileName': stl_filename,
                            'bodyName': body.name,
                            'material': item['material'],
                            'color': extract_body_color(body),
                            'quantity': item['quantity'],
                            'dimensions': {'width': w, 'depth': d, 'height': h}
                        })

                    # Requester & Project Profile Info
                    requester_name = ''
                    requester_email = ''
                    team_name = ''
                    project_name = design_name

                    try:
                        u = getattr(_app, 'currentUser', None)
                        if u:
                            requester_name = getattr(u, 'displayName', '') or getattr(u, 'userName', '')
                            requester_email = getattr(u, 'email', '')
                    except Exception:
                        pass

                    if not requester_name:
                        try:
                            requester_name = getpass.getuser().capitalize()
                        except Exception:
                            pass

                    today = datetime.date.today()
                    tomorrow = today + datetime.timedelta(days=1)

                    profile_info = {
                        'requester': requester_name,
                        'email': requester_email,
                        'project': project_name,
                        'team': team_name,
                        'date': tomorrow.strftime('%Y-%m-%d'),
                        'exportedDate': today.strftime('%Y-%m-%d'),
                        'designName': design_name,
                        'notes': f'Exported from Autodesk Fusion 360 ({design_name})'
                    }

                    manifest_data = {
                        'sessionId': session_id,
                        'designName': design_name,
                        'exportedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                        'count': len(manifest_parts),
                        'profile': profile_info,
                        'parts': manifest_parts
                    }

                    with open(os.path.join(temp_dir, 'manifest.json'), 'w', encoding='utf-8') as f:
                        json.dump(manifest_data, f, indent=2)

                    port = start_bridge_server(temp_dir)
                    if not port:
                        if _ui:
                            _ui.messageBox('Failed to start local bridge server.', '3D Print Builder')
                        return

                    bridge_source = f'http://127.0.0.1:{port}/'
                    encoded_source = urllib.parse.quote(bridge_source)
                    base_url = payload.get('webappUrl') or 'http://localhost:8000'
                    full_web_url = f'{base_url}/?source={encoded_source}'

                    try:
                        subprocess.Popen(['powershell.exe', '-NoProfile', '-Command', f'Set-Clipboard -Value "{full_web_url}"'])
                    except Exception:
                        pass

                    try:
                        webbrowser.open(full_web_url, new=2)
                    except Exception:
                        try:
                            os.startfile(full_web_url)
                        except Exception:
                            pass

                    if auto_close and self._palette:
                        self._palette.isVisible = False

            except Exception:
                if _ui:
                    _ui.messageBox(f'Export error:\n{traceback.format_exc()}', '3D Print Builder')

# ── Command Execution Handlers ───────────────────────────────────────────────
if IN_FUSION:
    class CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
        def __init__(self):
            super().__init__()

        def notify(self, args):
            try:
                cmd = args.command
                if cmd:
                    cmd.isAutoExecute = True
                show_palette()
            except Exception:
                if _ui:
                    _ui.messageBox(f'Failed to open 3D Print Builder:\n{traceback.format_exc()}', 'Error')

def show_palette():
    global _palette, _handlers, _ui, _app
    try:
        if not _ui:
            return

        existing = _ui.palettes.itemById(PALETTE_ID)
        if existing:
            try:
                existing.deleteMe()
            except Exception:
                pass
        pal = None
        addon_dir = os.path.dirname(os.path.realpath(__file__))
        html_path = os.path.join(addon_dir, 'resources', 'dialog.html')

        if not pal:
            pal = _ui.palettes.add(
                PALETTE_ID,
                PALETTE_NAME,
                PALETTE_URL,
                True,   # isVisible
                False,  # showCloseButton (False removes redundant native bottom Close button)
                True,   # isResizable
                460,    # width
                740,    # height
                True    # useNewWebBrowser (Qt WebEngine)
            )
            pal.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight

            # Wire Active Selection Watcher first
            sel_watcher = ActiveSelectionChangedHandler(pal)
            _ui.activeSelectionChanged.add(sel_watcher)
            _handlers.append(sel_watcher)

            # Wire HTML Event Handler with watcher reference
            html_handler = PaletteHTMLEventHandler(pal, sel_watcher)
            pal.incomingFromHTML.add(html_handler)
            _handlers.append(html_handler)

            _palette = pal
        else:
            _palette = pal

        _palette.isVisible = True

        # Send fresh data
        info = get_current_design_info()
        initial_bodies = []
        if _ui and _ui.activeSelections and _ui.activeSelections.count > 0:
            initial_bodies = resolve_all_bodies_from_selection(_ui.activeSelections)
        if not initial_bodies and _app and _app.activeProduct:
            initial_bodies = collect_all_visible_bodies(_app.activeProduct)

        sel_watcher.selected_bodies = list(initial_bodies)

        if _ui and initial_bodies:
            sel_watcher.suppress = True
            try:
                _ui.activeSelections.clear()
                for b in initial_bodies:
                    try:
                        _ui.activeSelections.add(b)
                    except Exception:
                        pass
                if _app and _app.activeViewport:
                    _app.activeViewport.refresh()
            finally:
                sel_watcher.suppress = False

        _palette.sendInfoToHTML('init', json.dumps(info))

    except Exception:
        if _ui:
            _ui.messageBox(f'Failed to show palette:\n{traceback.format_exc()}', 'Error')

def clean_old_controls(ui):
    try:
        all_ids_to_clean = OLD_CMD_IDS + [CMD_ID, 'FabLabToolsDropdown', 'FabLabToolsDropdown_v7', 'FabLabDropdown']
        for panel in ui.allToolbarPanels:
            for cid in all_ids_to_clean:
                try:
                    ctrl = panel.controls.itemById(cid)
                    while ctrl:
                        ctrl.deleteMe()
                        ctrl = panel.controls.itemById(cid)
                except Exception:
                    pass

        workspace = ui.workspaces.itemById('FusionSolidEnvironment')
        if workspace:
            for tab_id in ['UtilitiesTab', 'ToolsTab', 'SolidTab']:
                tab = workspace.toolbarTabs.itemById(tab_id)
                if tab:
                    for pid in ['FabLabToolbarPanel', 'FabLabPanel', 'FabLabSolidPanel']:
                        p = tab.toolbarPanels.itemById(pid)
                        if p:
                            try:
                                p.deleteMe()
                            except Exception:
                                pass

        for cid in all_ids_to_clean:
            cmd_def = ui.commandDefinitions.itemById(cid)
            if cmd_def:
                try:
                    cmd_def.deleteMe()
                except Exception:
                    pass
    except Exception:
        pass

def run(context):
    global _app, _ui
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface

        clean_old_controls(_ui)

        addon_dir = os.path.dirname(os.path.realpath(__file__))
        resource_dir = os.path.join(addon_dir, 'resources')
        if not os.path.exists(resource_dir):
            resource_dir = ''

        # Command Definition
        cmd_def = _ui.commandDefinitions.itemById(CMD_ID)
        if not cmd_def:
            cmd_def = _ui.commandDefinitions.addButtonDefinition(
                CMD_ID,
                CMD_NAME,
                CMD_DESCRIPTION,
                resource_dir
            )

        on_created = CommandCreatedHandler()
        cmd_def.commandCreated.add(on_created)
        _handlers.append(on_created)

        # Dedicated "FABLAB" Panel on Utilities Tab
        workspace = _ui.workspaces.itemById('FusionSolidEnvironment')
        if not workspace:
            return

        util_tab = workspace.toolbarTabs.itemById('UtilitiesTab')
        if not util_tab:
            util_tab = workspace.toolbarTabs.itemById('ToolsTab')
        if not util_tab:
            return

        fablab_panel = util_tab.toolbarPanels.itemById(CUSTOM_PANEL_ID)
        if not fablab_panel:
            try:
                fablab_panel = util_tab.toolbarPanels.add(CUSTOM_PANEL_ID, CUSTOM_PANEL_NAME, 'SelectPanel', False)
            except Exception:
                try:
                    fablab_panel = util_tab.toolbarPanels.add(CUSTOM_PANEL_ID, CUSTOM_PANEL_NAME)
                except Exception:
                    fablab_panel = None

        if fablab_panel:
            btn = fablab_panel.controls.itemById(CMD_ID)
            if not btn:
                btn = fablab_panel.controls.addCommand(cmd_def)
            if btn:
                btn.isPromoted = True
                btn.isPromotedByDefault = True

    except Exception:
        if _ui:
            _ui.messageBox(f'Failed to load FabLab 3D Print Bridge:\n{traceback.format_exc()}', 'Error')

def stop(context):
    global _app, _ui, _handlers, _palette
    try:
        if _ui:
            clean_old_controls(_ui)
            if _palette:
                _palette.deleteMe()
                _palette = None

        stop_bridge_server()
        _handlers.clear()
        _cached_bodies.clear()
    except Exception:
        pass
