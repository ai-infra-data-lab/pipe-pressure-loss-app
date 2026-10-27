"""Offscreen Qt integration checks; not evidence of native manual operation."""
import csv
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).parent
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['MPLCONFIGDIR']=str(ROOT/'artifacts'/'matplotlib')

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from examples import full_demo,straight,symmetric
from model import Element, load_project
from solver import solve
from ui import COLUMNS, FieldEditor, MainWindow, METRICS


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')
        (ROOT/'artifacts').mkdir(exist_ok=True)

    def setUp(self):
        self.w=MainWindow(full_demo())
        self.messages=[]
        self.w.error=lambda message:self.messages.append(message)
        self.w.show()
        self.app.processEvents()

    def tearDown(self):
        if self.w.worker:
            self.w.worker.cancel.set()
            self.w.worker.wait(5000)
            self.app.processEvents()
        self.w.saved_state=self.w.project.to_dict()
        if self.w.editor:self.w.editor.pending=False
        self.w.close()
        self.app.processEvents()

    def test_all_views_and_screenshots(self):
        self.w.accept_result(solve(self.w.project))
        self.app.processEvents()
        self.assertEqual(self.w.results_table.rowCount(),13)
        for i in range(len(METRICS)):
            self.w.metric_combo.setCurrentIndex(i)
        self.w.metric_combo.setCurrentIndex(0)
        self.w.select('FILTER')
        self.app.processEvents()
        self.assertTrue(self.w.grab().save(str(ROOT/'artifacts'/'screen_results.png')))
        self.w.tabs.setCurrentIndex(2)
        self.w.graph_type.setCurrentIndex(0)
        self.w.graph_via.setCurrentText('F-OUT')
        self.app.processEvents()
        self.assertTrue(self.w.grab().save(str(ROOT/'artifacts'/'screen_graph.png')))
        self.w.figure.savefig(ROOT/'artifacts'/'pressure_graph.png',dpi=140)
        self.w.graph_type.setCurrentIndex(1)
        self.w.plot_results()
        self.w.figure.savefig(ROOT/'artifacts'/'loss_graph.png',dpi=140)
        self.w.graph_type.setCurrentIndex(2)
        self.w.figure.savefig(ROOT/'artifacts'/'filter_graph.png',dpi=140)
        self.assertEqual(self.messages,[])

    def test_edit_invalidates_and_undo_redo(self):
        self.w.accept_result(solve(self.w.project))
        self.w.select('SMALL')
        self.w.editor.widgets['length_m'].setValue(4)
        self.w.apply_editor()
        self.assertIsNone(self.w.result)
        self.assertEqual(self.w.project.elements[6].length_m,4)
        self.w.undo()
        self.assertEqual(self.w.project.elements[6].length_m,2)
        self.w.redo()
        self.assertEqual(self.w.project.elements[6].length_m,4)

    def test_editor_switch_auto_applies(self):
        self.w.select('SMALL')
        self.w.editor.widgets['length_m'].setValue(3)
        self.w.select('FEED')
        self.assertEqual(self.w.project.elements[6].length_m,3)
        self.assertEqual(self.w.selection,'FEED')

    def test_malformed_curve_is_not_silently_applied(self):
        self.w.select('FILTER')
        self.w.editor.widgets['curve'].setPlainText('not JSON')
        self.w.apply_editor()
        self.assertTrue(self.messages)
        self.assertEqual(len(self.w.project.elements[2].curve),4)

    def test_table_selection_links_inspector(self):
        self.w.accept_result(solve(self.w.project))
        self.w.results_table.selectRow(2)
        self.app.processEvents()
        self.assertEqual(self.w.selection,'FILTER')
        self.assertIn('FILTER',self.w.inspector_title.text())

    def test_scene_selection_does_not_delete_active_item(self):
        self.w.network.items_by_id['SMALL'][0].setSelected(True)
        self.app.processEvents()
        self.assertEqual(self.w.selection,'SMALL')

    def test_layout_move_keeps_hydraulics(self):
        self.w.accept_result(solve(self.w.project))
        result=self.w.result
        self.w.move_node('IN',-10,190)
        self.assertIs(self.w.result,result)
        self.assertEqual(self.w.project.nodes[0].x,-10)
        self.w.undo()
        self.assertEqual(self.w.project.nodes[0].x,0)

    def test_mouse_drag_from_unselected_node(self):
        view=self.w.network
        item=view.items_by_id['S-C'][0]
        start=view.mapFromScene(item.pos())
        end=start+QPoint(35,-25)
        original=self.w.project.nodes[1].x
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=start)
        self.app.processEvents()
        QTest.mouseMove(view.viewport(),end,20)
        self.app.processEvents()
        QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=end)
        self.app.processEvents()
        self.assertNotEqual(self.w.project.nodes[1].x,original)

    def test_model_save_open_via_ui(self):
        target=ROOT/'artifacts'/'保存モデル.json'
        with patch('ui.QFileDialog.getSaveFileName',return_value=(str(target),'JSON (*.json)')):
            self.assertTrue(self.w.save(True))
        self.assertEqual(load_project(target).to_dict(),self.w.project.to_dict())
        self.w.project=straight()
        self.w.saved_state=self.w.project.to_dict()
        with patch('ui.QFileDialog.getOpenFileName',return_value=(str(target),'JSON (*.json)')):
            self.w.open_project()
        self.assertEqual(len(self.w.project.tees),2)
        self.assertEqual(self.messages,[])

    def test_background_calculation(self):
        self.w.calculate()
        deadline=time.monotonic()+10
        while self.w.worker is not None and time.monotonic()<deadline:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertIsNone(self.w.worker)
        self.assertIsNotNone(self.w.result)
        self.assertTrue(self.w.run_button.isEnabled())
        self.assertTrue(self.w.centralWidget().isEnabled())

    def test_csv_values_and_validity_metadata(self):
        self.w.accept_result(solve(self.w.project))
        path=ROOT/'artifacts'/'results.csv'
        self.w.write_csv(path)
        with path.open(encoding='utf-8-sig',newline='') as stream: rows=list(csv.reader(stream))
        header=next(i for i,row in enumerate(rows) if row and row[0]=='ID')
        self.assertEqual(len(rows)-header-1,13)
        column=[key for key,_ in COLUMNS].index('loss_kpa')
        self.assertAlmostEqual(float(rows[header+1][column]),self.w.result['rows'][0]['loss_kpa'])
        self.assertTrue(any('SHA256' in ' '.join(row) for row in rows))

    def test_new_add_delete_and_boundary_protection(self):
        node=self.w.project.nodes[0]
        self.w.selection=node.id
        self.w.delete_selected()
        self.assertTrue(self.messages)
        self.assertEqual(len(self.w.project.nodes),13)

    def test_filter_missing_data_marks_unsupported(self):
        self.w.project.elements[2].curve_viscosity_cst=46
        self.w.accept_result(solve(self.w.project))
        self.assertFalse(self.w.result['valid'])
        self.assertIn('適用外',self.w.state_label.text())

    def test_fixed_color_range_and_reference_selection(self):
        self.w.accept_result(solve(self.w.project))
        self.w.fixed_range.setChecked(True)
        self.w.high.setValue(50)
        self.w.refresh_network()
        self.assertIn('50',self.w.legend.text())

    def test_elbow_add_button_and_editor_angle_limit(self):
        self.w.replace_project(straight())
        captured=[]
        def choose(obj,title):
            captured.append(obj)
            obj.angle_deg=180
            return obj
        self.w.object_dialog=choose
        self.w.add_elbow()
        self.assertEqual(len(captured),1)
        self.assertEqual(captured[0].kind,'elbow')
        self.assertEqual(captured[0].roughness_mm,0)
        self.assertEqual(self.w.project.elements[-1].angle_deg,180)
        self.assertEqual(self.w.editor.widgets['angle_deg'].minimum(),0)
        self.assertEqual(self.w.editor.widgets['angle_deg'].maximum(),180)

    def test_elbow_editor_switches_modes_and_syncs_diameter(self):
        e=Element('E1','IN','OUT',kind='elbow',roughness_mm=0)
        editor=FieldEditor(e,straight())
        editor.widgets['diameter_mm'].setValue(32)
        self.assertEqual(editor.value().outlet_diameter_mm,32)
        self.assertTrue(editor.widgets['k'].isHidden())
        editor.widgets['elbow_mode'].setCurrentIndex(1)
        self.assertFalse(editor.widgets['k'].isHidden())
        editor.deleteLater()

    def test_elbow_results_map_graph_and_csv(self):
        p=straight()
        p.fluid.viscosity_cst=1
        e=p.elements[0]
        e.kind='elbow'
        e.angle_deg=180
        e.roughness_mm=0
        self.w.replace_project(p)
        self.w.accept_result(solve(p))
        keys=[k for k,_ in COLUMNS]
        self.assertEqual(self.w.results_table.item(0,keys.index('angle_deg')).text(),'180')
        self.assertEqual(self.w.results_table.item(0,keys.index('kind')).text(),'エルボ')
        self.w.select('P1')
        self.w.tabs.setCurrentIndex(2)
        self.app.processEvents()
        self.assertTrue(self.w.grab().save(str(ROOT/'artifacts'/'screen_elbow.png')))
        self.w.write_csv(ROOT/'artifacts'/'elbow_results.csv')
        self.assertIn('Rennels',(ROOT/'artifacts'/'elbow_results.csv').read_text(encoding='utf-8-sig'))


if __name__=='__main__':
    unittest.main(verbosity=2)
