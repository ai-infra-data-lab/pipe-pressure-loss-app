from __future__ import annotations

import copy
import csv
import json
import math
import os
from pathlib import Path
import threading

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGraphicsEllipseItem, QGraphicsItem,
    QGraphicsScene, QGraphicsView, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

import matplotlib
matplotlib.use('QtAgg')
from matplotlib import colormaps, rcParams
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure

from examples import SAMPLES, full_demo
from model import Element, Fluid, ModelError, Node, Project, Tee, load_project, project_from_dict, save_project
from solver import Cancelled, route_rows, solve, validate

rcParams['font.family'] = ['Yu Gothic', 'Meiryo', 'DejaVu Sans']
rcParams['axes.unicode_minus'] = False

KIND = {'pipe':'直管','local':'汎用K部品','expansion':'急拡大','contraction':'急縮小','filter':'フィルター','elbow':'エルボ',
        'split':'分岐（三ポート）','merge':'合流（三ポート）','flow':'流量境界','pressure':'静圧境界','junction':'接続節点'}
LABELS = {
 'id':'ID','kind':'種類','start':'入口節点','end':'出口節点','x':'図のX座標','y':'図のY座標',
 'elevation_m':'実際の高さ [m]','flow_lpm':'外部からの流入 [L/min]','pressure_kpa':'静圧 [kPaG]',
 'boundary_diameter_mm':'境界内径 [mm]（0=タンク）','diameter_mm':'入口内径 [mm]',
 'outlet_diameter_mm':'出口内径 [mm]','length_m':'直管長 [m]','roughness_mm':'絶対粗さ [mm]',
 'k':'K（空欄=未設定／拡大は自動）','reference_diameter_mm':'K参照内径 [mm]',
 'source':'係数・特性の出典','re_min':'適用Re 下限','re_max':'適用Re 上限',
 'allow_estimate':'概算を明示的に許可','fixed_flow_lpm':'指定モードの流量 [L/min]',
 'filter_mode':'フィルター入力方式','curve':'特性点 [[L/min,kPa], ...]',
 'fixed_dp_kpa':'指定差圧 [kPa]','fixed_at_lpm':'指定差圧の運転流量 [L/min]',
 'curve_viscosity_cst':'曲線基準動粘度 [cSt]','bypass_kpa':'バイパス差圧 [kPa]（0=未指定）',
 'common':'共通側ポート','run':'直進側ポート','branch':'側枝ポート',
 'common_diameter_mm':'共通側内径 [mm]','run_diameter_mm':'直進側内径 [mm]',
 'branch_diameter_mm':'側枝内径 [mm]','k_run':'直進経路K','k_branch':'側枝経路K',
 'reference':'Kの速度基準','fixed_run_lpm':'指定直進流量 [L/min]','fixed_branch_lpm':'指定側枝流量 [L/min]',
 'name':'油の名称','temperature_c':'油温 [℃]','density_kg_m3':'密度 [kg/m³]','viscosity_cst':'動粘度 [cSt]'}
LABELS.update({'angle_deg':'曲がり角度 [°]（0–180）','bend_radius_ratio':'中心線曲げ半径比 R/D','elbow_mode':'エルボの計算方式'})
METRICS = {'部品損失 [kPa]':'loss_kpa','流量 [L/min]':'flow_lpm','流速（Kは参照速度）[m/s]':'velocity_m_s',
           'Reynolds数':'re','損失勾配 [kPa/m]':'gradient_kpa_m','入口静圧 [kPaG]':'inlet_kpa',
           '出口静圧 [kPaG]':'outlet_kpa'}
COLUMNS = [('id','ID'),('kind','種類'),('start','入口'),('end','出口'),('diameter_mm','入口径 mm'),
 ('outlet_diameter_mm','出口径 mm'),('length_m','長さ m'),('flow_lpm','流量 L/min'),
 ('velocity_m_s','速度 m/s'),('re','Re'),('regime','流動区分'),('friction','Darcy f'),('k','K'),
 ('angle_deg','角度 °'),('bend_radius_ratio','曲げ R/D'),
 ('reference_diameter_mm','参照径 mm'),('loss_kpa','損失 kPa'),('inlet_kpa','入口 kPaG'),
 ('outlet_kpa','出口 kPaG'),('power_w','損失 W'),('status','適用状態'),('model','計算モデル'),('source','出典'),('messages','診断')]


_fonts_loaded=False


def configure_fonts():
    # Qt offscreen may not discover Windows fonts. Register installed fonts
    # directly; font files are neither copied nor distributed with this app.
    global _fonts_loaded
    if _fonts_loaded:return
    folder=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
    for name in ('meiryo.ttc','YuGothM.ttc','segoeui.ttf'):
        path=folder/name
        if path.exists():QFontDatabase.addApplicationFont(str(path))
    QApplication.instance().setFont(QFont('Meiryo',9))
    _fonts_loaded=True


def number(value, decimals=4):
    if value is None:
        return '—'
    if isinstance(value,(int,float)):
        return f'{value:,.{decimals}f}'.rstrip('0').rstrip('.') if decimals else f'{value:,.0f}'
    return str(value)


def spin(value, minimum=-1e12, maximum=1e12, decimals=6):
    widget = QDoubleSpinBox()
    widget.setRange(minimum,maximum)
    widget.setDecimals(decimals)
    widget.setValue(value)
    widget.setKeyboardTracking(False)
    widget.setMinimumWidth(110)
    return widget


class FieldEditor(QWidget):
    changed = Signal()

    def __init__(self,obj,project,existing=True,parent=None):
        super().__init__(parent)
        self.original = copy.deepcopy(obj)
        self.widgets = {}
        self.pending = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2,2,2,2)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        base,extra = QFormLayout(),QFormLayout()
        for title,form in [('基本・接続',base),('係数・特性',extra)]:
            inner=QWidget()
            inner.setLayout(form)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            scroll=QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(inner)
            tabs.addTab(scroll,title)
        if isinstance(obj,Node):
            keys=['id','kind','flow_lpm','pressure_kpa','boundary_diameter_mm','elevation_m','x','y']
        elif isinstance(obj,Tee):
            keys=['id','kind','common','run','branch','common_diameter_mm','run_diameter_mm','branch_diameter_mm',
                  'fixed_run_lpm','fixed_branch_lpm','k_run','k_branch','reference','source','re_min','re_max','allow_estimate']
        elif isinstance(obj,Fluid):
            keys=['name','temperature_c','density_kg_m3','viscosity_cst']
        else:
            keys=['id','kind','start','end','diameter_mm','outlet_diameter_mm','elbow_mode','angle_deg','bend_radius_ratio','length_m','roughness_mm','fixed_flow_lpm',
                  'k','reference_diameter_mm','source','re_min','re_max','allow_estimate',
                  'filter_mode','curve','curve_viscosity_cst','fixed_dp_kpa','fixed_at_lpm','bypass_kpa']
        extra_keys={'k','k_run','k_branch','reference','reference_diameter_mm','source','re_min','re_max',
                    'allow_estimate','filter_mode','curve','curve_viscosity_cst','fixed_dp_kpa','fixed_at_lpm','bypass_kpa'}
        self.forms = {}
        for key in keys:
            value=getattr(obj,key)
            choices=None
            if key=='kind':
                choices=['junction','flow','pressure'] if isinstance(obj,Node) else ['split','merge'] if isinstance(obj,Tee) else ['pipe','elbow','local','expansion','contraction','filter']
            elif key in ('start','end','common','run','branch'):
                choices=[n.id for n in project.nodes]
            elif key=='reference':
                choices=['common','path']
            elif key=='filter_mode':
                choices=['curve','fixed']
            elif key=='elbow_mode':
                choices=['rennels','specified_k']
            if choices is not None:
                widget=QComboBox()
                for choice in choices:
                    widget.addItem(KIND.get(choice,{'common':'共通側流速','path':'各経路側流速','curve':'Q–差圧曲線','fixed':'一運転点の指定差圧','rennels':'丸形エルボ：角度から自動K','specified_k':'入力角度に対する指定K'}.get(choice,choice)),choice)
                widget.setCurrentIndex(max(0,widget.findData(value)))
                widget.currentIndexChanged.connect(self.mark_changed)
            elif key in ('k','k_run','k_branch'):
                widget=QLineEdit('' if value is None else str(value))
                widget.setPlaceholderText('未設定')
                widget.textEdited.connect(self.mark_changed)
            elif isinstance(value,bool):
                widget=QCheckBox('許可する（結果に概算と表示）')
                widget.setChecked(value)
                widget.toggled.connect(self.mark_changed)
            elif isinstance(value,(float,int)):
                widget=spin(value,0,180,3) if key=='angle_deg' else spin(value,0.500001,1e6,6) if key=='bend_radius_ratio' else spin(value)
                widget.valueChanged.connect(self.mark_changed)
            elif key=='curve':
                widget=QPlainTextEdit(json.dumps(value,ensure_ascii=False))
                widget.setMinimumHeight(100)
                widget.textChanged.connect(self.mark_changed)
            else:
                widget=QLineEdit(value)
                widget.textEdited.connect(self.mark_changed)
                if key=='id' and existing:
                    widget.setReadOnly(True)
            form=extra if key in extra_keys else base
            form.addRow(LABELS[key],widget)
            self.forms[key]=form
            self.widgets[key]=widget
        if 'kind' in self.widgets:
            self.widgets['kind'].currentIndexChanged.connect(self.update_visibility)
        if 'filter_mode' in self.widgets:
            self.widgets['filter_mode'].currentIndexChanged.connect(self.update_visibility)
        if 'elbow_mode' in self.widgets:
            self.widgets['elbow_mode'].currentIndexChanged.connect(self.update_visibility)
            self.widgets['diameter_mm'].valueChanged.connect(self.sync_elbow_diameter)
        self.update_visibility()
        if isinstance(obj,(Element,Tee)):
            label=QLabel()
            self.model_note=label
            label.setWordWrap(True)
            label.setStyleSheet('color:#875500;padding:6px')
            layout.addWidget(label)
            self.update_visibility()

    def mark_changed(self,*_):
        self.pending=True
        self.changed.emit()

    def update_visibility(self,*_):
        if isinstance(self.original,Node):
            kind=self.widgets['kind'].currentData()
            hidden={'flow_lpm'} if kind!='flow' else set()
            if kind!='pressure': hidden.add('pressure_kpa')
            if kind=='junction': hidden.add('boundary_diameter_mm')
        elif isinstance(self.original,Element):
            kind=self.widgets['kind'].currentData()
            hidden=set()
            if kind!='pipe': hidden.add('length_m')
            if kind not in ('pipe','elbow'): hidden.add('roughness_mm')
            if kind not in ('local','expansion','contraction'): hidden.update(['k','reference_diameter_mm','re_min','re_max'])
            if kind!='elbow': hidden.update(['angle_deg','bend_radius_ratio','elbow_mode'])
            else:
                if self.widgets['elbow_mode'].currentData()=='specified_k':
                    hidden.difference_update(['k','re_min','re_max'])
                    hidden.add('roughness_mm')
                else:
                    hidden.add('source')
                self.sync_elbow_diameter()
            self.widgets['outlet_diameter_mm'].setEnabled(kind!='elbow')
            if hasattr(self,'model_note'):
                self.model_note.setText('丸形・同径エルボ、0–180°。Rは管中心線の半径。\n自動Kは曲管摩擦込みです。弧長を直管に重複計上しないでください。\n低Re・粗面は適用未確認。概算は明示選択してください。' if kind=='elbow' else 'Kは自動推定されません（急拡大の乱流式を除く）。\n出典・適用Re・参照速度を確認してください。')
            if kind!='local': hidden.add('reference_diameter_mm')
            if kind!='filter':
                hidden.update(['filter_mode','curve','curve_viscosity_cst','fixed_dp_kpa','fixed_at_lpm','bypass_kpa'])
            elif self.widgets['filter_mode'].currentData()=='curve': hidden.update(['fixed_dp_kpa','fixed_at_lpm'])
            else: hidden.update(['curve','curve_viscosity_cst'])
        else:
            hidden=set()
        for key,widget in self.widgets.items():
            self.forms[key].setRowVisible(widget,key not in hidden)

    def sync_elbow_diameter(self,*_):
        if self.widgets['kind'].currentData()=='elbow':
            self.widgets['outlet_diameter_mm'].setValue(self.widgets['diameter_mm'].value())

    def value(self):
        obj=copy.deepcopy(self.original)
        for key,w in self.widgets.items():
            if isinstance(w,QComboBox): value=w.currentData()
            elif isinstance(w,QCheckBox): value=w.isChecked()
            elif isinstance(w,QDoubleSpinBox): value=w.value()
            elif isinstance(w,QPlainTextEdit):
                try: value=json.loads(w.toPlainText())
                except ValueError as exc: raise ModelError('特性点は [[0,0],[10,20]] のようにJSON形式で入力してください') from exc
            elif key in ('k','k_run','k_branch'):
                try: value=float(w.text()) if w.text().strip() else None
                except ValueError as exc: raise ModelError('Kには数値または空欄を指定してください') from exc
            else: value=w.text().strip()
            setattr(obj,key,value)
        return obj


class NodeItem(QGraphicsEllipseItem):
    def __init__(self,node,view):
        super().__init__(-8,-8,16,16)
        self.node_id=node.id
        self.view=view
        self.initial=None
        self.setBrush(QColor('#ffffff'))
        self.setPen(QPen(QColor('#183a55'),2))
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable|QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setPos(node.x,node.y)
        self.setZValue(4)

    def mousePressEvent(self,event):
        self.initial=self.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        super().mouseReleaseEvent(event)
        position=self.pos()
        if self.initial is not None and (position-self.initial).manhattanLength()>0.1:
            self.view.node_moved.emit(self.node_id,position.x(),position.y())


class NetworkView(QGraphicsView):
    selected=Signal(str)
    node_moved=Signal(str,float,float)
    released=Signal()

    def __init__(self):
        super().__init__()
        self._scene=QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QColor('#f4f8fb'))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.items_by_id={}
        self.result_labels=[]
        self.scene().selectionChanged.connect(self.pick)

    def pick(self):
        selected=self.scene().selectedItems()
        if selected:
            item=selected[0]
            identifier=item.data(0)
            if identifier:
                self.selected.emit(identifier)

    def wheelEvent(self,event):
        scale=1.15 if event.angleDelta().y()>0 else 1/1.15
        if 0.05 < self.transform().m11()*scale < 10:
            self.scale(scale,scale)

    def mouseReleaseEvent(self,event):
        super().mouseReleaseEvent(event)
        self.released.emit()

    def highlight(self,identifier):
        for owner,items in self.items_by_id.items():
            for item in items:
                if hasattr(item,'line'):
                    pen=item.pen()
                    pen.setWidth(7 if owner==identifier else 5)
                    item.setPen(pen)

    def reset_results(self):
        for owner,items in self.items_by_id.items():
            for item in items:
                if hasattr(item,'line'):
                    pen=item.pen()
                    pen.setColor(QColor('#93a4b1'))
                    item.setPen(pen)
        for identifier,item in self.result_labels:
            item.setPlainText(identifier+'\n未計算')

    def fit(self):
        self.fitInView(self.scene().itemsBoundingRect().adjusted(-35,-40,35,40),Qt.AspectRatioMode.KeepAspectRatio)

    def render_network(self,project,result,metric,limits=None,selected=None):
        self.scene().blockSignals(True)
        self.scene().clear()
        self.items_by_id={}
        self.result_labels=[]
        nodes={n.id:n for n in project.nodes}
        rows={r['id']:r for r in result['rows']} if result else {}
        angles={e.id:e.angle_deg for e in project.elements if e.kind=='elbow'}
        values=[r.get(metric) for r in rows.values() if r['status']!='適用外' and r.get(metric) is not None]
        low,high=limits if limits else (min(values),max(values)) if values else (0,1)
        divergent=low<0<high
        if divergent:
            bound=max(abs(low),abs(high))
            low,high=-bound,bound
        def color(row):
            if not row or row['status']=='適用外' or row.get(metric) is None:
                return QColor('#93a4b1')
            fraction=(row[metric]-low)/(high-low) if high>low else 0.5
            rgba=colormaps['coolwarm' if divergent else 'viridis'](max(0,min(1,fraction)))
            return QColor.fromRgbF(*rgba[:3])
        def line(identifier,owner,a,b,description):
            if a not in nodes or b not in nodes: return
            p1=QPointF(nodes[a].x,nodes[a].y)
            p2=QPointF(nodes[b].x,nodes[b].y)
            row=rows.get(identifier)
            pen=QPen(color(row),7 if owner==selected else 5)
            if row and row['status']=='適用外': pen.setStyle(Qt.PenStyle.DashLine)
            item=self.scene().addLine(p1.x(),p1.y(),p2.x(),p2.y(),pen)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            item.setData(0,owner)
            item.setToolTip(description+'\n'+(row['messages'] if row else '未計算'))
            self.items_by_id.setdefault(owner,[]).append(item)
            angle=math.atan2(p2.y()-p1.y(),p2.x()-p1.x())
            if row and row['flow_lpm']<0: angle+=math.pi
            middle=(p1+p2)/2
            arrow=QPolygonF([middle+QPointF(10*math.cos(angle),10*math.sin(angle)),
                middle+QPointF(9*math.cos(angle+2.5),9*math.sin(angle+2.5)),
                middle+QPointF(9*math.cos(angle-2.5),9*math.sin(angle-2.5))])
            self.scene().addPolygon(arrow,QPen(color(row)),color(row))
            value=number(row.get(metric),3) if row else '未計算'
            warning=' ⚠' if row and row['status']!='計算済み' else ''
            caption=identifier+(f' {angles[owner]:g}°' if owner in angles else '')
            text=self.scene().addText(caption+'\n'+value+warning,QFont('Meiryo',10))
            text.setDefaultTextColor(QColor('#17324b'))
            text.setPos(middle.x()-25,middle.y()-46)
            text.setZValue(3)
            self.result_labels.append((caption,text))
        for e in project.elements:
            line(e.id,e.id,e.start,e.end,KIND[e.kind]+(f' {e.angle_deg:g}° / R/D={e.bend_radius_ratio:g}' if e.kind=='elbow' else ''))
        for t in project.tees:
            for leg,port in [('run',t.run),('branch',t.branch)]:
                a,b=(t.common,port) if t.kind=='split' else (port,t.common)
                line(t.id+':'+leg,t.id,a,b,KIND[t.kind])
        nrows={r['id']:r for r in result['nodes']} if result else {}
        for n in project.nodes:
            item=NodeItem(n,self)
            item.setData(0,n.id)
            if n.kind=='flow': item.setBrush(QColor('#c9e6ff'))
            if n.kind=='pressure': item.setBrush(QColor('#f9d7a9'))
            self.scene().addItem(item)
            self.items_by_id[n.id]=[item]
            text=self.scene().addText(n.id,QFont('Meiryo',9))
            text.setPos(n.x-18,n.y+8)
            if n.id in nrows:
                item.setToolTip(f"{n.id}\n静圧 {number(nrows[n.id]['pressure_kpa'])} kPaG\n高さ {n.elevation_m:g} m")
        self.scene().setSceneRect(self.scene().itemsBoundingRect().adjusted(-80,-60,80,60))
        self.scene().blockSignals(False)
        return low,high


class SolverThread(QThread):
    completed=Signal(object)
    failed=Signal(str)

    def __init__(self,project):
        super().__init__()
        self.project=project
        self.cancel=threading.Event()

    def run(self):
        try: self.completed.emit(solve(self.project,self.cancel))
        except (ModelError,Cancelled) as exc: self.failed.emit(str(exc))
        except Exception as exc: self.failed.emit(f'計算中の例外: {type(exc).__name__}: {exc}')


class MainWindow(QMainWindow):
    def __init__(self,project=None):
        super().__init__()
        configure_fonts()
        self.project=project or full_demo()
        self.result=None
        self.worker=None
        self.generation=0
        self.undo_history=[]
        self.redo_history=[]
        self.saved_state=self.project.to_dict()
        self.path=None
        self.editor=None
        self.selection=None
        self.syncing=False
        self.resize(1500,950)
        self.setMinimumSize(1050,720)
        self.build_ui()
        self.refresh_document(fit=True)

    def action(self,menu,text,callback,shortcut=None):
        action=QAction(text,self)
        if shortcut: action.setShortcut(shortcut)
        action.triggered.connect(callback)
        menu.addAction(action)
        return action

    def build_ui(self):
        file=self.menuBar().addMenu('ファイル')
        self.action(file,'新規',self.new_project,'Ctrl+N')
        self.action(file,'開く…',self.open_project,'Ctrl+O')
        self.action(file,'保存',self.save,'Ctrl+S')
        self.action(file,'名前を付けて保存…',lambda:self.save(True),'Ctrl+Shift+S')
        self.action(file,'結果CSV…',self.export_csv)
        self.action(file,'系統図PNG…',self.export_map)
        self.action(file,'グラフPNG…',self.export_plot)
        edit=self.menuBar().addMenu('編集')
        self.undo_action=self.action(edit,'元に戻す',self.undo,'Ctrl+Z')
        self.redo_action=self.action(edit,'やり直し',self.redo,'Ctrl+Y')
        self.action(edit,'選択部品を削除',self.delete_selected)
        display=self.menuBar().addMenu('表示')
        self.action(display,'系統図を全体表示',lambda:self.network.fit(),'Ctrl+0')
        help_menu=self.menuBar().addMenu('ヘルプ')
        self.action(help_menu,'計算モデルと使い方',self.show_help)
        toolbar=QToolBar('計算操作')
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.sample=QComboBox()
        self.sample.addItems(SAMPLES)
        toolbar.addWidget(self.sample)
        load=QPushButton('サンプルを開く')
        self.sample_load_button=load
        load.clicked.connect(self.load_sample)
        toolbar.addWidget(load)
        toolbar.addSeparator()
        self.check_button=QPushButton('入力確認')
        self.check_button.clicked.connect(self.check_input)
        toolbar.addWidget(self.check_button)
        self.run_button=QPushButton('▶ 計算')
        self.run_button.setObjectName('calculate')
        self.run_button.clicked.connect(self.calculate)
        toolbar.addWidget(self.run_button)
        self.cancel_button=QPushButton('中断')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_calculation)
        toolbar.addWidget(self.cancel_button)

        root=QWidget()
        self.setCentralWidget(root)
        layout=QVBoxLayout(root)
        layout.setContentsMargins(12,8,12,8)
        header=QHBoxLayout()
        self.title_label=QLabel()
        self.title_label.setStyleSheet('font-size:18px;font-weight:600;color:#153c58')
        header.addWidget(self.title_label,1)
        self.state_label=QLabel('未計算')
        header.addWidget(self.state_label)
        layout.addLayout(header)
        settings=QHBoxLayout()
        self.mode=QComboBox()
        self.mode.addItem('流量配分を自動計算','auto')
        self.mode.addItem('全枝の流量を指定','specified')
        self.mode.currentIndexChanged.connect(self.mode_changed)
        settings.addWidget(self.mode)
        self.fluid_label=QLabel()
        settings.addWidget(self.fluid_label,1)
        fluid=QPushButton('油・物性を編集')
        fluid.clicked.connect(self.edit_fluid)
        settings.addWidget(fluid)
        layout.addLayout(settings)
        self.note_label=QLabel()
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet('background:#fff4d6;color:#674b0a;padding:7px;border-radius:4px')
        layout.addWidget(self.note_label)

        vertical=QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(vertical,1)
        horizontal=QSplitter()
        vertical.addWidget(horizontal)
        left=QWidget()
        left_layout=QVBoxLayout(left)
        left_layout.setContentsMargins(0,0,0,0)
        left_layout.addWidget(QLabel('モデルの部品'))
        self.tree=QTreeWidget()
        self.tree.setHeaderLabels(['ID / 部品'])
        self.tree.itemSelectionChanged.connect(self.tree_selected)
        left_layout.addWidget(self.tree,1)
        for title,callback in [('＋ 節点・境界',self.add_node),('＋ 配管・径変化・フィルター',self.add_element),('＋ エルボ（0–180°）',self.add_elbow),
                               ('＋ 三ポート分岐・合流',self.add_tee),('選択部品を削除',self.delete_selected)]:
            button=QPushButton(title)
            button.clicked.connect(callback)
            left_layout.addWidget(button)
        horizontal.addWidget(left)

        center=QWidget()
        center_layout=QVBoxLayout(center)
        center_layout.setContentsMargins(5,0,5,0)
        colors=QHBoxLayout()
        self.metric_combo=QComboBox()
        self.metric_combo.addItems(METRICS)
        self.metric_combo.currentIndexChanged.connect(self.refresh_network)
        colors.addWidget(self.metric_combo,1)
        fit=QPushButton('全体表示')
        fit.clicked.connect(lambda:self.network.fit())
        colors.addWidget(fit)
        center_layout.addLayout(colors)
        limits=QHBoxLayout()
        self.fixed_range=QCheckBox('色範囲を固定')
        self.low=spin(0)
        self.high=spin(100)
        self.fixed_range.toggled.connect(self.refresh_network)
        self.low.valueChanged.connect(self.refresh_network)
        self.high.valueChanged.connect(self.refresh_network)
        limits.addWidget(self.fixed_range)
        limits.addWidget(self.low)
        limits.addWidget(QLabel('〜'))
        limits.addWidget(self.high)
        center_layout.addLayout(limits)
        self.network=NetworkView()
        self.network.selected.connect(self.select,Qt.ConnectionType.QueuedConnection)
        self.network.node_moved.connect(self.move_node,Qt.ConnectionType.QueuedConnection)
        self.network.released.connect(self.refresh_network,Qt.ConnectionType.QueuedConnection)
        center_layout.addWidget(self.network,1)
        self.color_bar=QLabel()
        self.color_bar.setFixedHeight(8)
        center_layout.addWidget(self.color_bar)
        self.legend=QLabel()
        self.legend.setWordWrap(True)
        center_layout.addWidget(self.legend)
        hint=QLabel('節点をドラッグして配置／ホイールで拡大・縮小。図の位置は実長・高さと独立です。')
        hint.setStyleSheet('color:#627485;font-size:11px')
        center_layout.addWidget(hint)
        horizontal.addWidget(center)

        right=QWidget()
        self.inspector_layout=QVBoxLayout(right)
        self.inspector_layout.setContentsMargins(3,0,0,0)
        self.inspector_title=QLabel('部品を選択')
        self.inspector_title.setStyleSheet('font-weight:600')
        self.inspector_layout.addWidget(self.inspector_title)
        self.editor_holder=QWidget()
        self.editor_layout=QVBoxLayout(self.editor_holder)
        self.editor_layout.setContentsMargins(0,0,0,0)
        self.inspector_layout.addWidget(self.editor_holder,1)
        self.apply_button=QPushButton('入力を適用')
        self.apply_button.clicked.connect(self.apply_editor)
        self.inspector_layout.addWidget(self.apply_button)
        self.selected_result=QLabel('結果は計算後に表示')
        self.selected_result.setWordWrap(True)
        self.inspector_layout.addWidget(self.selected_result)
        horizontal.addWidget(right)
        horizontal.setSizes([205,900,330])
        horizontal.setStretchFactor(1,1)

        self.tabs=QTabWidget()
        vertical.addWidget(self.tabs)
        self.results_table=QTableWidget()
        self.results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results_table.itemSelectionChanged.connect(self.table_selected)
        self.tabs.addTab(self.results_table,'配管・部品の結果')
        self.nodes_table=QTableWidget()
        self.nodes_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tabs.addTab(self.nodes_table,'節点・境界')
        graph=QWidget()
        gl=QVBoxLayout(graph)
        controls=QHBoxLayout()
        self.graph_type=QComboBox()
        self.graph_type.addItems(['経路の圧力・損失','部品別の損失','フィルター特性と運転点'])
        self.graph_start=QComboBox()
        self.graph_end=QComboBox()
        self.graph_via=QComboBox()
        self.graph_type.currentIndexChanged.connect(self.plot_results)
        for w in (self.graph_start,self.graph_end,self.graph_via):
            w.currentIndexChanged.connect(self.plot_results)
        controls.addWidget(self.graph_type,2)
        for label,w in [('開始',self.graph_start),('経由',self.graph_via),('終点',self.graph_end)]:
            controls.addWidget(QLabel(label))
            controls.addWidget(w,1)
        gl.addLayout(controls)
        self.figure=Figure(figsize=(10,3),layout='constrained')
        self.canvas=FigureCanvasQTAgg(self.figure)
        gl.addWidget(NavigationToolbar2QT(self.canvas,self))
        gl.addWidget(self.canvas,1)
        self.tabs.addTab(graph,'グラフ')
        self.log=QPlainTextEdit()
        self.log.setReadOnly(True)
        self.tabs.addTab(self.log,'診断・計算情報')
        vertical.setSizes([510,290])
        vertical.setStretchFactor(0,2)
        vertical.setStretchFactor(1,1)
        self.statusBar().showMessage('未計算。サンプルは仮想条件です。')

    def snapshot(self):
        self.undo_history.append(self.project.to_dict())
        self.undo_history=self.undo_history[-80:]
        self.redo_history.clear()

    def invalidate(self):
        self.generation+=1
        self.result=None
        if self.worker: self.worker.cancel.set()
        self.state_label.setText('再計算が必要')
        self.state_label.setStyleSheet('color:#945b00;font-weight:600')
        self.results_table.setRowCount(0)
        self.nodes_table.setRowCount(0)
        self.network.reset_results()
        self.figure.clear()
        self.canvas.draw_idle()
        self.selected_result.setText('条件を変更しました。再計算してください。')
        self.log.setPlainText('入力が変更されました。旧結果は表示・出力できません。')
        self.update_title()

    def update_title(self):
        dirty=self.project.to_dict()!=self.saved_state or bool(self.editor and self.editor.pending)
        self.setWindowTitle(('＊ ' if dirty else '')+self.project.title+' | 油配管の圧力損失')
        self.undo_action.setEnabled(bool(self.undo_history))
        self.redo_action.setEnabled(bool(self.redo_history))

    def refresh_document(self,fit=False):
        self.syncing=True
        self.title_label.setText(self.project.title)
        f=self.project.fluid
        self.fluid_label.setText(f'{f.name} ｜ {f.temperature_c:g} ℃ ｜ ρ {f.density_kg_m3:g} kg/m³ ｜ ν {f.viscosity_cst:g} cSt')
        self.mode.setCurrentIndex(self.mode.findData(self.project.mode))
        self.note_label.setText(self.project.notes or '定常・単相・一定温度の油。物性と係数は使用条件に合わせて入力してください。')
        self.tree.clear()
        for title,items in [('節点・境界',self.project.nodes),('配管・部品',self.project.elements),('分岐・合流',self.project.tees)]:
            parent=QTreeWidgetItem([title])
            self.tree.addTopLevelItem(parent)
            for obj in items:
                item=QTreeWidgetItem([obj.id+'  '+KIND.get(obj.kind,obj.kind)])
                item.setData(0,Qt.ItemDataRole.UserRole,obj.id)
                parent.addChild(item)
            parent.setExpanded(True)
        old_start=self.graph_start.currentText()
        old_end=self.graph_end.currentText()
        old_via=self.graph_via.currentText()
        for w in (self.graph_start,self.graph_end,self.graph_via): w.clear()
        ids=[n.id for n in self.project.nodes]
        self.graph_start.addItems(ids)
        self.graph_end.addItems(ids)
        self.graph_via.addItems(['指定なし']+ids)
        if ids:
            self.graph_start.setCurrentText(old_start if old_start in ids else ids[0])
            self.graph_end.setCurrentText(old_end if old_end in ids else ids[-1])
            self.graph_via.setCurrentText(old_via if old_via in ids else '指定なし')
        self.syncing=False
        self.refresh_network()
        if fit: QTimer.singleShot(0,self.network.fit)
        self.build_inspector()
        self.update_title()

    def find_object(self,identifier=None):
        return next((obj for obj in [*self.project.nodes,*self.project.elements,*self.project.tees] if obj.id==(identifier or self.selection)),None)

    def build_inspector(self):
        while self.editor_layout.count():
            item=self.editor_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.editor=None
        obj=self.find_object()
        if obj:
            self.inspector_title.setText(obj.id+' ｜ '+KIND[obj.kind]+(f' {obj.angle_deg:g}° / R/D={obj.bend_radius_ratio:g}' if isinstance(obj,Element) and obj.kind=='elbow' else ''))
            self.editor=FieldEditor(obj,self.project)
            self.editor.changed.connect(self.pending_input)
            self.editor_layout.addWidget(self.editor)
        else:
            self.inspector_title.setText('図・一覧から部品を選択')
        self.apply_button.setEnabled(obj is not None)
        rows=[r for r in self.result['rows'] if r['owner']==self.selection] if self.result else []
        self.selected_result.setText('\n'.join(f"{r['id']}: {number(r['flow_lpm'])} L/min\n損失 {number(r['loss_kpa'])} kPa ｜ {r['status']}" for r in rows) or '結果は計算後に表示')

    def flush_editor(self):
        if not self.editor or not self.editor.pending: return True
        try:
            obj=self.editor.value()
            # Apply local edits even while the network is incomplete. Whole-model
            # validation runs before calculation; malformed fields are caught here.
            if not obj.id or ':' in obj.id: raise ModelError('IDは空欄・コロンを使用できません')
            self.snapshot()
            collection=self.project.nodes if isinstance(obj,Node) else self.project.tees if isinstance(obj,Tee) else self.project.elements
            index=next(i for i,x in enumerate(collection) if x.id==self.selection)
            collection[index]=obj
            self.editor.original=copy.deepcopy(obj)
            self.editor.pending=False
            self.invalidate()
            return True
        except (ModelError,ValueError) as exc:
            self.error(str(exc))
            return False

    def pending_input(self):
        self.state_label.setText('入力編集中（適用後に再計算）')
        self.state_label.setStyleSheet('color:#945b00;font-weight:600')
        self.update_title()

    def apply_editor(self):
        if self.flush_editor(): self.refresh_document()

    def select(self,identifier):
        if self.syncing or identifier==self.selection: return
        if not self.flush_editor(): return
        self.selection=identifier
        self.build_inspector()
        self.syncing=True
        for parent_index in range(self.tree.topLevelItemCount()):
            parent=self.tree.topLevelItem(parent_index)
            for j in range(parent.childCount()):
                item=parent.child(j)
                if item.data(0,Qt.ItemDataRole.UserRole)==identifier:
                    self.tree.setCurrentItem(item)
        for i in range(self.results_table.rowCount()):
            if self.results_table.item(i,0).data(Qt.ItemDataRole.UserRole)==identifier:
                self.results_table.selectRow(i)
                break
        self.syncing=False
        self.network.highlight(identifier)
        if QApplication.mouseButtons()==Qt.MouseButton.NoButton:
            self.refresh_network()

    def tree_selected(self):
        if self.syncing:return
        item=self.tree.currentItem()
        if item and item.data(0,Qt.ItemDataRole.UserRole): self.select(item.data(0,Qt.ItemDataRole.UserRole))

    def table_selected(self):
        if self.syncing:return
        i=self.results_table.currentRow()
        if i>=0: self.select(self.results_table.item(i,0).data(Qt.ItemDataRole.UserRole))

    def move_node(self,identifier,x,y):
        if not self.flush_editor(): return
        self.snapshot()
        node=self.find_object(identifier)
        node.x,node.y=round(x,2),round(y,2)
        # Layout changes preserve hydraulic results; they still need saving.
        self.selection=identifier
        self.refresh_network()
        self.build_inspector()
        self.update_title()

    def refresh_network(self,*_):
        if self.syncing or not hasattr(self,'network'):return
        limits=(self.low.value(),self.high.value()) if self.fixed_range.isChecked() else None
        if limits and limits[0]>=limits[1]:
            self.legend.setText('色範囲は 下限 < 上限 にしてください')
            return
        low,high=self.network.render_network(self.project,self.result,METRICS[self.metric_combo.currentText()],limits,self.selection)
        invalid=' ｜ 適用外の値は灰色・破線' if self.result and not self.result['valid'] else ''
        cmap='coolwarm' if low<0<high else 'viridis'
        stops=', '.join(f'stop:{i/4} {QColor.fromRgbF(*colormaps[cmap](i/4)[:3]).name()}' for i in range(5))
        self.legend.setText(f'{self.metric_combo.currentText()}： {number(low)}  →  {number(high)}　｜ 灰色 = 未計算・未評価'+invalid)
        self.color_bar.setStyleSheet(f'background:qlineargradient(x1:0,y1:0,x2:1,y2:0,{stops});border-radius:3px')
        self.legend.setStyleSheet('color:#253e51;font-size:11px')

    def mode_changed(self):
        if self.syncing:return
        if not self.flush_editor():return
        self.snapshot()
        self.project.mode=self.mode.currentData()
        self.invalidate()
        self.refresh_network()

    def object_dialog(self,obj,title):
        dialog=QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(460,690)
        layout=QVBoxLayout(dialog)
        editor=FieldEditor(obj,self.project,existing=False)
        layout.addWidget(editor)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        def accept():
            try:
                value=editor.value()
                if hasattr(value,'id') and (not value.id or ':' in value.id or self.find_object(value.id)):
                    raise ModelError('IDは空欄・重複・コロンを使用できません')
                dialog.value=value
                dialog.accept()
            except (ModelError,ValueError) as exc:self.error(str(exc))
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        return dialog.value if dialog.exec()==QDialog.DialogCode.Accepted else None

    def new_id(self,prefix):
        i=1
        while self.find_object(f'{prefix}{i}'):i+=1
        return f'{prefix}{i}'

    def add_node(self):
        if not self.flush_editor():return
        n=len(self.project.nodes)
        obj=self.object_dialog(Node(self.new_id('N'),x=n*90,y=100),'節点・境界を追加')
        if obj:self.add_object(obj,self.project.nodes)

    def add_element(self):
        if not self.flush_editor():return
        if len(self.project.nodes)<2:return self.error('先に節点を2個以上追加してください')
        ids=[n.id for n in self.project.nodes]
        a=self.selection if self.selection in ids else ids[0]
        b=next(x for x in ids if x!=a)
        obj=self.object_dialog(Element(self.new_id('P'),a,b),'配管・径変化・フィルターを追加')
        if obj:self.add_object(obj,self.project.elements)

    def add_tee(self):
        if not self.flush_editor():return
        if len(self.project.nodes)<3:return self.error('先に接続ポート用の節点を3個以上追加してください')
        ids=[n.id for n in self.project.nodes]
        obj=self.object_dialog(Tee(self.new_id('T'),*ids[:3]),'三ポート分岐・合流を追加')
        if obj:self.add_object(obj,self.project.tees)

    def add_elbow(self):
        if not self.flush_editor():return
        if len(self.project.nodes)<2:return self.error('先に接続用の節点を2個以上追加してください')
        ids=[n.id for n in self.project.nodes]
        a=self.selection if self.selection in ids else ids[0]
        b=next(x for x in ids if x!=a)
        obj=self.object_dialog(Element(self.new_id('E'),a,b,kind='elbow',roughness_mm=0.0),'エルボを追加（0–180°）')
        if obj:self.add_object(obj,self.project.elements)

    def add_object(self,obj,collection):
        self.snapshot()
        collection.append(obj)
        self.selection=obj.id
        self.invalidate()
        self.refresh_document(fit=True)

    def edit_fluid(self):
        if not self.flush_editor():return
        obj=self.object_dialog(self.project.fluid,'油・物性（運転温度の物性値を入力）')
        if obj:
            self.snapshot()
            self.project.fluid=obj
            self.invalidate()
            self.refresh_document()

    def delete_selected(self):
        if not self.flush_editor():return
        obj=self.find_object()
        if not obj:return
        if isinstance(obj,Node) and any(obj.id in (e.start,e.end) for e in self.project.elements):
            return self.error('接続中の節点です。先に接続部品を削除・変更してください')
        if isinstance(obj,Node) and any(obj.id in (t.common,t.run,t.branch) for t in self.project.tees):
            return self.error('分岐・合流に接続中です。先に接続を変更してください')
        self.snapshot()
        collection=self.project.nodes if isinstance(obj,Node) else self.project.tees if isinstance(obj,Tee) else self.project.elements
        collection.remove(obj)
        self.selection=None
        self.invalidate()
        self.refresh_document()

    def undo(self):
        if not self.flush_editor():return
        if not self.undo_history:return
        self.redo_history.append(self.project.to_dict())
        self.project=project_from_dict(self.undo_history.pop())
        self.invalidate()
        self.refresh_document()

    def redo(self):
        if not self.flush_editor():return
        if not self.redo_history:return
        self.undo_history.append(self.project.to_dict())
        self.project=project_from_dict(self.redo_history.pop())
        self.invalidate()
        self.refresh_document()

    def may_replace(self):
        if not self.flush_editor():return False
        if self.project.to_dict()==self.saved_state:return True
        choice=QMessageBox.question(self,'変更を保存','現在の変更を保存しますか？',
            QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel)
        if choice==QMessageBox.StandardButton.Cancel:return False
        if choice==QMessageBox.StandardButton.Save:return self.save()
        return True

    def replace_project(self,project,path=None):
        self.project=project
        self.path=path
        self.saved_state=project.to_dict()
        self.undo_history.clear()
        self.redo_history.clear()
        self.selection=None
        self.editor=None
        self.invalidate()
        self.refresh_document(fit=True)

    def new_project(self):
        if self.may_replace():self.replace_project(Project())

    def load_sample(self):
        if self.may_replace():self.replace_project(SAMPLES[self.sample.currentText()]())

    def open_project(self):
        if not self.may_replace():return
        path,_=QFileDialog.getOpenFileName(self,'配管モデルを開く',str(Path(__file__).parent/'examples'),'JSON (*.json)')
        if not path:return
        try:
            p=load_project(path)
            # Validate types safely; incomplete models can still be edited.
            self.replace_project(p,Path(path))
        except (OSError,ValueError,TypeError) as exc:self.error(str(exc))

    def save(self,save_as=False):
        if not self.flush_editor():return False
        path=self.path
        if save_as or path is None:
            folder=Path(__file__).parent/'local-data'
            folder.mkdir(exist_ok=True)
            value,_=QFileDialog.getSaveFileName(self,'モデルを保存',str(folder/'配管モデル.json'),'JSON (*.json)')
            if not value:return False
            path=Path(value)
        try:
            save_project(self.project,path)
            self.path=path
            self.saved_state=self.project.to_dict()
            self.update_title()
            self.statusBar().showMessage(f'保存しました: {path}')
            return True
        except (OSError,ValueError) as exc:
            self.error(str(exc))
            return False

    def check_input(self):
        if not self.flush_editor():return
        try:
            messages=validate(self.project)
            self.log.setPlainText('接続・境界条件の基本確認：OK\n計数の適用状態は計算後に判定します。\n'+'\n'.join(messages))
            self.tabs.setCurrentWidget(self.log)
        except (ModelError,TypeError,ValueError) as exc:self.error(str(exc))

    def calculate(self):
        if self.worker or not self.flush_editor():return
        try:validate(self.project)
        except (ModelError,TypeError,ValueError) as exc:return self.error(str(exc))
        self.result=None
        self.results_table.setRowCount(0)
        self.nodes_table.setRowCount(0)
        self.refresh_network()
        generation=self.generation
        self.worker=SolverThread(self.project.clone())
        self.worker.completed.connect(lambda r:self.accept_result(r,generation))
        self.worker.failed.connect(self.calculation_failed)
        self.worker.finished.connect(self.worker_finished)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.centralWidget().setEnabled(False)
        self.menuBar().setEnabled(False)
        self.sample.setEnabled(False)
        self.sample_load_button.setEnabled(False)
        self.check_button.setEnabled(False)
        self.state_label.setText('計算中…')
        self.worker.start()

    def cancel_calculation(self):
        if self.worker:self.worker.cancel.set()

    def worker_finished(self):
        if self.worker:self.worker.deleteLater()
        self.worker=None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.centralWidget().setEnabled(True)
        self.menuBar().setEnabled(True)
        self.sample.setEnabled(True)
        self.sample_load_button.setEnabled(True)
        self.check_button.setEnabled(True)

    def calculation_failed(self,message):
        self.state_label.setText('計算未完了')
        self.log.setPlainText(message)
        self.statusBar().showMessage(message)
        self.tabs.setCurrentWidget(self.log)

    def accept_result(self,result,generation=None):
        if generation is not None and generation!=self.generation:return
        self.result=result
        valid=result['valid']
        caution=bool(result['warnings'])
        self.state_label.setText('収束・適用外あり' if not valid else '収束・要確認あり' if caution else '収束・計算済み')
        self.state_label.setStyleSheet('color:'+('#a13322' if not valid else '#906000' if caution else '#117354')+';font-weight:600')
        self.fill_tables()
        self.refresh_network()
        self.build_inspector()
        text=[f"計算器 {result['version']} ｜ 反復評価回数 {result['evaluations']}",
              f"最大無次元残差 {result['residual']:.3g}",f"連続式残差 {result['mass_residual_lpm']:.3g} L/min",
              f"エネルギー式残差 {result['energy_residual_pa']:.3g} Pa",
              f"損失動力（全枝のΣ QΔp） {result['loss_power_w']:.6g} W",
              f"境界の正味エネルギー流入 {result['boundary_power_w']:.6g} W",
              '適用状態：'+('全てのモデルが適用可（要確認項目は下記）' if valid else '適用外項目あり。表示は参考値です。')]
        text+=['\n【適用外】']+result['errors'] if result['errors'] else []
        text+=['\n【要確認・概算】']+result['warnings'] if result['warnings'] else []
        text+=['\n【モデル注記】',self.project.notes]
        self.log.setPlainText('\n'.join(text))
        self.statusBar().showMessage(f"計算終了 ｜ 損失動力 {number(result['loss_power_w'])} W ｜ 要確認 {len(result['warnings'])} 件 ｜ 適用外 {len(result['errors'])} 件")
        self.plot_results()

    def fill_tables(self):
        self.syncing=True
        self.results_table.setColumnCount(len(COLUMNS))
        self.results_table.setHorizontalHeaderLabels([title for _,title in COLUMNS])
        self.results_table.setRowCount(len(self.result['rows']))
        for i,row in enumerate(self.result['rows']):
            for j,(key,_) in enumerate(COLUMNS):
                value=KIND.get(row[key],row[key]) if key=='kind' else row[key]
                item=QTableWidgetItem(number(value))
                item.setData(Qt.ItemDataRole.UserRole,row['owner'])
                item.setToolTip(row['messages'] or row['model'])
                if row['status']=='適用外':item.setBackground(QColor('#fbe0dc'))
                elif row['status']=='要確認':item.setBackground(QColor('#fff3d4'))
                self.results_table.setItem(i,j,item)
        self.results_table.resizeColumnsToContents()
        columns=[('id','節点'),('kind','種類'),('pressure_kpa','静圧 kPaG'),('energy_kpa','機械エネルギー圧 kPa'),('elevation_m','高さ m'),('external_flow_lpm','外部流入 L/min')]
        self.nodes_table.setColumnCount(len(columns))
        self.nodes_table.setHorizontalHeaderLabels([x[1] for x in columns])
        self.nodes_table.setRowCount(len(self.result['nodes']))
        for i,row in enumerate(self.result['nodes']):
            for j,(key,_) in enumerate(columns):
                self.nodes_table.setItem(i,j,QTableWidgetItem(number(KIND.get(row[key],row[key]) if key=='kind' else row[key])))
        self.nodes_table.resizeColumnsToContents()
        self.syncing=False

    def plot_results(self,*_):
        if self.syncing or not hasattr(self,'figure'):return
        self.figure.clear()
        ax=self.figure.add_subplot(111)
        if not self.result:
            ax.text(.5,.5,'計算後にグラフを表示します',ha='center',va='center',transform=ax.transAxes)
            self.canvas.draw_idle()
            return
        warning='（概算・要確認を含む）' if self.result['warnings'] else ''
        if not self.result['valid']:warning='（適用外を含む参考値）'
        kind=self.graph_type.currentIndex()
        if kind==1:
            rows=self.result['rows']
            ax.bar([r['id'] for r in rows],[r['loss_kpa'] for r in rows],color=['#8293a1' if r['status']=='適用外' else '#327ba1' for r in rows],picker=True)
            ax.set_ylabel('不可逆損失 / 経路損失 [kPa]')
            ax.tick_params(axis='x',labelrotation=30,labelsize=8)
            ax.set_title('各部品・経路の損失 '+warning,fontsize=10)
        elif kind==2:
            found=False
            for e in self.project.elements:
                if e.kind!='filter':continue
                if e.filter_mode=='curve' and e.curve:
                    ax.plot([p[0] for p in e.curve],[p[1] for p in e.curve],label=e.id+' 入力曲線',marker='.')
                row=next(r for r in self.result['rows'] if r['id']==e.id)
                ax.scatter([abs(row['flow_lpm'])],[row['loss_kpa']],label=e.id+' 運転点',zorder=4)
                if e.bypass_kpa>0:ax.axhline(e.bypass_kpa,ls='--',label=e.id+' バイパス設定')
                found=True
            ax.set_xlabel('流量 [L/min]')
            ax.set_ylabel('フィルター差圧 [kPa]')
            ax.set_title('フィルター特性 '+warning if found else 'フィルターがありません',fontsize=10)
            if found:ax.legend(fontsize=8)
        else:
            start,end=self.graph_start.currentText(),self.graph_end.currentText()
            via=self.graph_via.currentText()
            path=(route_rows(self.result,start,via)+route_rows(self.result,via,end)) if via!='指定なし' else route_rows(self.result,start,end)
            if via!='指定なし' and ((start!=via and not route_rows(self.result,start,via)) or (via!=end and not route_rows(self.result,via,end))):path=[]
            if path:
                xs,ps,totals,losses=[],[],[],[]
                distance,cumulative=0.,0.
                for row,forward in path:
                    pi,po=(row['inlet_kpa'],row['outlet_kpa']) if forward else (row['outlet_kpa'],row['inlet_kpa'])
                    ti,to=(row['inlet_total_kpa'],row['outlet_total_kpa']) if forward else (row['outlet_total_kpa'],row['inlet_total_kpa'])
                    xs.extend([distance,distance+row['length_m']])
                    ps.extend([pi,po])
                    totals.extend([ti,to])
                    losses.extend([cumulative,cumulative+row['loss_kpa']])
                    distance+=row['length_m']
                    cumulative+=row['loss_kpa']
                ax.plot(xs,ps,label='静圧 [kPaG]',marker='.',lw=1.5)
                ax.plot(xs,totals,label='全圧（α補正）[kPaG]',ls='--')
                ax.plot(xs,losses,label='経路の累積損失 [kPa]',ls=':')
                ax.legend(fontsize=8)
                ax.set_xlabel('経路の実長 [m]（集中損失は段差）')
                ax.set_ylabel('圧力 / 損失 [kPa]')
                ax.set_title(start+' → '+end+' '+warning,fontsize=10)
            else:ax.text(.5,.5,'指定した流向の経路がありません',ha='center',transform=ax.transAxes)
        ax.grid(True,alpha=.2)
        self.canvas.draw_idle()

    def export_ready(self):
        if not self.flush_editor():return False
        if self.result is None:
            self.error('現在の条件で計算してから出力してください')
            return False
        return True

    def write_csv(self,path):
        with open(path,'w',newline='',encoding='utf-8-sig') as stream:
            writer=csv.writer(stream)
            writer.writerow(['タイトル',self.project.title])
            writer.writerow(['計算器',self.result['version'],'適用状態','適用可' if self.result['valid'] else '適用外あり・参考値'])
            writer.writerow(['計算日時 UTC',self.result['calculated_at'],'入力 SHA256',self.result['input_sha256']])
            writer.writerow(['油温 ℃',self.project.fluid.temperature_c,'密度 kg/m3',self.project.fluid.density_kg_m3,'動粘度 cSt',self.project.fluid.viscosity_cst])
            writer.writerow(['注記',self.project.notes])
            writer.writerow([title for _,title in COLUMNS])
            # Quoting does not stop Excel formula evaluation. Escape text fields.
            def safe(value):
                return "'"+value if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')) else value
            for row in self.result['rows']:
                writer.writerow([safe(row[key]) for key,_ in COLUMNS])

    def export_csv(self):
        if not self.export_ready():return
        path,_=QFileDialog.getSaveFileName(self,'結果CSV',str(Path(__file__).parent/'結果.csv'),'CSV (*.csv)')
        if path:
            try:self.write_csv(path)
            except OSError as exc:self.error(str(exc))

    def export_map(self):
        if not self.export_ready():return
        path,_=QFileDialog.getSaveFileName(self,'系統図PNG',str(Path(__file__).parent/'系統図.png'),'PNG (*.png)')
        if path:
            # Capture the complete map panel, including metric, units, legend,
            # and validity status. No unlabeled bare colored lines.
            self.network.fit()
            if not self.network.parentWidget().grab().save(path):self.error('画像を保存できません')

    def export_plot(self):
        if not self.export_ready():return
        path,_=QFileDialog.getSaveFileName(self,'グラフPNG',str(Path(__file__).parent/'グラフ.png'),'PNG (*.png)')
        if path:
            try:self.figure.savefig(path,dpi=160)
            except OSError as exc:self.error(str(exc))

    def show_help(self):
        dialog=QDialog(self)
        dialog.setWindowTitle('計算モデルと使い方')
        dialog.resize(800,650)
        layout=QVBoxLayout(dialog)
        text=QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText((Path(__file__).parent/'README.md').read_text(encoding='utf-8'))
        layout.addWidget(text)
        dialog.exec()

    def error(self,message):
        self.statusBar().showMessage(message)
        QMessageBox.warning(self,'入力・計算の確認',message)

    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():
            self.worker.cancel.set()
            if not self.worker.wait(3000):
                self.statusBar().showMessage('計算を中断中です。少し待ってから閉じてください。')
                event.ignore()
                return
        if self.may_replace():event.accept()
        else:event.ignore()
