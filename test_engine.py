import math
import tempfile
import threading
import unittest
from pathlib import Path

from examples import elbow_demo, full_demo, straight, symmetric
from hydraulics import colebrook, element_loss, friction_factor, kinetic, rounded_elbow_k
from model import Element, Fluid, ModelError, Node, Project, load_project, project_from_dict, save_project
from solver import Cancelled, route_rows, solve


class EngineeringTests(unittest.TestCase):
    def test_elbow_documented_reference(self):
        # Published fluids documentation example: Di=4.020, rc=20, 30°, Re=1e5.
        f=colebrook(1e5,0)
        self.assertAlmostEqual(rounded_elbow_k(30,20/4.020,f),0.11519070808085191,places=12)

    def test_elbow_angles_include_zero_and_180(self):
        for angle in [0,0.001,15,45,90,135,180]:
            with self.subTest(angle=angle):
                k=rounded_elbow_k(angle,1.5,0.02)
                self.assertTrue(math.isfinite(k))
                self.assertGreaterEqual(k,0)
        self.assertEqual(rounded_elbow_k(0,1.5,0.02),0)
        # 90°: sin(alpha/2)=sqrt(2)/2 and exponent=2.
        s=math.sqrt(2)/2
        expected=0.02*math.pi/2*1.5+(0.1+2.4*0.02)*s+6.6*0.02*(math.sqrt(s)+s)/1.5**2
        self.assertAlmostEqual(rounded_elbow_k(90,1.5,0.02),expected,places=14)

    def test_elbow_network_friction_included_once(self):
        p=straight()
        p.fluid.viscosity_cst=1
        e=p.elements[0]
        e.kind='elbow'
        e.angle_deg=180
        e.roughness_mm=0
        r=solve(p)
        row=r['rows'][0]
        expected_length=math.pi*1.5*0.025
        self.assertTrue(r['valid'],r['errors'])
        self.assertAlmostEqual(row['length_m'],expected_length)
        self.assertEqual(row['angle_deg'],180)
        self.assertEqual(row['bend_radius_ratio'],1.5)
        self.assertIn('Rennels',row['source'])
        v=(20/60000)/(math.pi*.025**2/4)
        self.assertAlmostEqual(row['loss_kpa']*1000,row['k']*850*v*v/2)
        self.assertAlmostEqual(row['inlet_kpa']-row['outlet_kpa'],row['loss_kpa'],places=8)
        # Changing the unrelated straight-pipe length does not double count it.
        e.length_m=1000
        self.assertAlmostEqual(solve(p)['rows'][0]['loss_kpa'],row['loss_kpa'])

    def test_elbow_low_re_and_roughness_require_estimate(self):
        e=Element('E','A','B',kind='elbow',roughness_mm=0)
        out=element_loss(e,20/60000,Fluid())
        self.assertTrue(out.errors)
        e.allow_estimate=True
        self.assertFalse(element_loss(e,20/60000,Fluid()).errors)
        self.assertTrue(element_loss(e,20/60000,Fluid()).warnings)
        e.allow_estimate=False
        e.roughness_mm=0.045
        self.assertTrue(element_loss(e,20/60000,Fluid(viscosity_cst=1)).errors)

    def test_elbow_zero_angle_and_zero_flow(self):
        e=Element('E','A','B',kind='elbow',roughness_mm=0,angle_deg=0)
        out=element_loss(e,20/60000,Fluid())
        self.assertEqual(out.pa,0)
        self.assertEqual(out.k,0)
        self.assertEqual(out.errors,[])
        e.angle_deg=180
        out=element_loss(e,0,Fluid())
        self.assertEqual(out.pa,0)
        self.assertTrue(math.isfinite(out.k))

    def test_elbow_reverse_flow(self):
        e=Element('E','A','B',kind='elbow',roughness_mm=0)
        f=Fluid(viscosity_cst=1)
        forward=element_loss(e,20/60000,f)
        backward=element_loss(e,-20/60000,f)
        self.assertAlmostEqual(forward.pa,-backward.pa)
        self.assertEqual(backward.errors,[])

    def test_elbow_manual_k_is_at_entered_angle(self):
        e=Element('E','A','B',kind='elbow',elbow_mode='specified_k',k=0.7,source='入力角度の実測値')
        out=element_loss(e,20/60000,Fluid())
        self.assertEqual(out.k,0.7)
        self.assertEqual(out.errors,[])
        e.source=''
        self.assertTrue(element_loss(e,20/60000,Fluid()).errors)

    def test_elbow_invalid_geometry(self):
        for angle,ratio in [(-1,1.5),(180.1,1.5),(90,0.5),(90,0)]:
            p=straight()
            p.elements[0].kind='elbow'
            p.elements[0].angle_deg=angle
            p.elements[0].bend_radius_ratio=ratio
            with self.assertRaises(ModelError):solve(p)

    def test_elbow_round_trip_and_legacy_defaults(self):
        p=straight()
        p.elements[0].kind='elbow'
        p.elements[0].angle_deg=135
        p.elements[0].bend_radius_ratio=2.5
        loaded=project_from_dict(p.to_dict())
        self.assertEqual(loaded.elements[0].angle_deg,135)
        self.assertEqual(loaded.elements[0].bend_radius_ratio,2.5)
        data=straight().to_dict()
        for key in ['angle_deg','bend_radius_ratio','elbow_mode']:data['elements'][0].pop(key)
        self.assertTrue(solve(project_from_dict(data))['valid'])

    def test_elbow_demonstration(self):
        r=solve(elbow_demo())
        self.assertTrue(r['valid'],r['errors'])
        self.assertEqual([row['angle_deg'] for row in r['rows']],[0,45,90,180])
        self.assertEqual(r['rows'][0]['loss_kpa'],0)
        self.assertTrue(r['warnings'])

    def test_poiseuille_and_diameter_power(self):
        p = straight()
        r = solve(p)
        q = 20/60000
        mu = 850*32e-6
        exact = 128*mu*10*q/(math.pi*0.025**4)
        self.assertAlmostEqual(r['rows'][0]['loss_kpa']*1000,exact,places=7)
        p.elements[0].diameter_mm = p.elements[0].outlet_diameter_mm = 12.5
        for n in p.nodes:
            n.boundary_diameter_mm = 12.5
        self.assertAlmostEqual(solve(p)['rows'][0]['loss_kpa']/r['rows'][0]['loss_kpa'],16,places=7)

    def test_colebrook_residual(self):
        for re,rough in [(1e5,0.001),(4e3,0),(1e7,0.01)]:
            f = colebrook(re,rough)
            residual = 1/math.sqrt(f)+2*math.log10(rough/3.7+2.51/re/math.sqrt(f))
            self.assertLess(abs(residual),1e-11)

    def test_turbulent_published_reference(self):
        # Common Moody/Colebrook checkpoint, independent expected value.
        self.assertAlmostEqual(colebrook(1e5,0.001),0.022174535944515,places=12)

    def test_transition_and_zero(self):
        self.assertEqual(friction_factor(0,0),(0,'停止'))
        self.assertEqual(friction_factor(3000,0)[1],'遷移域')
        p = straight()
        p.nodes[0].flow_lpm = 0
        r = solve(p)
        self.assertAlmostEqual(r['rows'][0]['flow_lpm'],0,places=8)
        self.assertAlmostEqual(r['rows'][0]['loss_kpa'],0,places=8)

    def test_static_pressure_boundary(self):
        p = straight()
        r = solve(p)
        row = r['rows'][0]
        self.assertAlmostEqual(row['outlet_kpa'],0,places=8)
        self.assertAlmostEqual(row['inlet_kpa'],row['loss_kpa'],places=8)

    def test_pressure_driven(self):
        p = straight()
        target = solve(p)['rows'][0]['loss_kpa']
        p.nodes[0].kind = 'pressure'
        p.nodes[0].pressure_kpa = target
        r = solve(p)
        self.assertAlmostEqual(r['rows'][0]['flow_lpm'],20,places=6)

    def test_symmetric_split_merge(self):
        r = solve(symmetric())
        rows = {v['id']:v for v in r['rows']}
        self.assertTrue(r['valid'])
        self.assertAlmostEqual(rows['UPPER']['flow_lpm'],10,places=7)
        self.assertAlmostEqual(rows['LOWER']['flow_lpm'],10,places=7)
        self.assertLess(r['mass_residual_lpm'],1e-7)
        self.assertAlmostEqual(r['loss_power_w'],r['boundary_power_w'],places=6)

    def test_asymmetric_laminar_parallel(self):
        p = symmetric()
        p.elements[2].length_m = 16
        rows = {v['id']:v for v in solve(p)['rows']}
        self.assertAlmostEqual(rows['UPPER']['flow_lpm']/rows['LOWER']['flow_lpm'],2,places=7)

    def test_specified_and_incompatible_loop(self):
        p = symmetric()
        p.mode = 'specified'
        self.assertTrue(solve(p)['valid'])
        p.elements[2].length_m = 16
        with self.assertRaisesRegex(ModelError,'不整合'):
            solve(p)

    def test_specified_mass_mismatch(self):
        p = symmetric()
        p.mode = 'specified'
        p.elements[0].fixed_flow_lpm = 15
        with self.assertRaisesRegex(ModelError,'連続式'):
            solve(p)

    def test_expansion_pressure_recovery(self):
        fluid = Fluid(viscosity_cst=1)
        q = 30/60000
        e = Element('E','A','B',kind='expansion',diameter_mm=10,outlet_diameter_mm=20)
        out = element_loss(e,q,fluid)
        v1,v2 = q/(math.pi*.01**2/4),q/(math.pi*.02**2/4)
        self.assertAlmostEqual(out.pa,fluid.density_kg_m3*(v1-v2)**2/2,places=7)
        static_recovery = kinetic(q,10,fluid)-kinetic(q,20,fluid)-out.pa
        self.assertGreater(static_recovery,0)
        self.assertEqual(out.errors,[])

    def test_low_re_expansion_rejected_unless_explicit(self):
        e = Element('E','A','B',kind='expansion',diameter_mm=20,outlet_diameter_mm=40)
        self.assertTrue(element_loss(e,10/60000,Fluid()).errors)
        e.allow_estimate = True
        self.assertFalse(element_loss(e,10/60000,Fluid()).errors)
        self.assertTrue(element_loss(e,10/60000,Fluid()).warnings)

    def test_curve_interpolation_and_domain(self):
        e = Element('F','A','B',kind='filter',curve=[[0,0],[10,20],[20,50]],source='検算曲線')
        self.assertAlmostEqual(element_loss(e,15/60000,Fluid()).pa,35000)
        self.assertTrue(element_loss(e,30/60000,Fluid()).errors)
        self.assertTrue(element_loss(e,10/60000,Fluid(viscosity_cst=46)).errors)

    def test_fixed_filter_is_one_operating_point(self):
        e = Element('F','A','B',kind='filter',filter_mode='fixed',fixed_at_lpm=10,fixed_dp_kpa=25,source='指定点')
        self.assertAlmostEqual(element_loss(e,10/60000,Fluid()).pa,25000)
        self.assertTrue(element_loss(e,12/60000,Fluid()).errors)

    def test_bypass_and_missing_k(self):
        e = Element('F','A','B',kind='filter',curve=[[0,0],[10,20]],source='試験',bypass_kpa=10)
        self.assertTrue(element_loss(e,10/60000,Fluid()).errors)
        e.kind = 'local'
        self.assertTrue(element_loss(e,10/60000,Fluid()).errors)

    def test_full_demo_and_routes(self):
        r = solve(full_demo())
        self.assertTrue(r['valid'],r['errors'])
        self.assertEqual(len(r['rows']),13)
        self.assertTrue(r['warnings'])
        self.assertAlmostEqual(r['loss_power_w'],r['boundary_power_w'],places=6)
        path = route_rows(r,'IN','OUT')
        self.assertTrue(path)
        self.assertEqual(path[-1][0]['end'],'OUT')

    def test_reverse_pipe(self):
        p = straight()
        p.elements[0].start,p.elements[0].end = 'OUT','IN'
        r = solve(p)
        self.assertAlmostEqual(r['rows'][0]['flow_lpm'],-20,places=7)
        self.assertGreater(r['rows'][0]['loss_kpa'],0)

    def test_elevation(self):
        p = straight()
        r1 = solve(p)
        p.nodes[1].elevation_m = 10
        r2 = solve(p)
        self.assertAlmostEqual(r2['rows'][0]['inlet_kpa']-r1['rows'][0]['inlet_kpa'],850*9.80665*10/1000,places=7)

    def test_negative_tee_coefficients_energy_check(self):
        p = symmetric()
        p.tees[0].k_run = -1
        p.tees[0].k_branch = -1
        r = solve(p)
        self.assertFalse(r['valid'])
        self.assertTrue(any('損失動力が負' in e for e in r['errors']))

    def test_validation(self):
        for change in ['reference','disconnected','diameter','curve']:
            p = full_demo()
            if change == 'reference':
                p.nodes[-1].kind='junction'
            elif change == 'disconnected':
                p.nodes.append(Node('ISOLATED'))
            elif change == 'diameter':
                p.elements[0].diameter_mm=-1
            else:
                p.elements[2].curve=[[0,0],[0,2]]
            with self.assertRaises(ModelError):
                solve(p)

    def test_round_trip_and_strict_loading(self):
        p = full_demo()
        scratch = Path(__file__).parent/'artifacts'/'test_tmp'
        scratch.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as folder:
            path = Path(folder)/'日本語配管.json'
            save_project(p,path)
            self.assertEqual(load_project(path).to_dict(),p.to_dict())
        data = p.to_dict()
        data['schema_version']=999
        with self.assertRaises(ModelError):
            project_from_dict(data)
        data=p.to_dict()
        data['fluid']['viscosity_cst']=float('nan')
        with self.assertRaises(ModelError):
            project_from_dict(data)

    def test_cancellation(self):
        cancel=threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            solve(full_demo(),cancel)


if __name__ == '__main__':
    unittest.main(verbosity=2)
