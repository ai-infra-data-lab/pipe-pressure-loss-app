"""Fictional demonstrations, never manufacturer or actual plant data."""
from model import Element, Fluid, Node, Project, Tee


def straight():
    return Project(title="直管の解析解検算（仮想条件）",fluid=Fluid("仮想油",40,850,32),
        nodes=[Node("IN","flow",0,0,flow_lpm=20,boundary_diameter_mm=25),Node("OUT","pressure",600,0,boundary_diameter_mm=25)],
        elements=[Element("P1","IN","OUT",diameter_mm=25,outlet_diameter_mm=25,length_m=10,fixed_flow_lpm=20)],
        notes="仮想物性・仮想寸法。層流直管をHagen–Poiseuille式で検算する例。出口は管断面のゲージ圧0 kPa。")


def symmetric():
    p = Project(title="対称分岐・再合流の検算（仮想条件）",fluid=Fluid("仮想油",40,850,32),
        nodes=[Node("IN","flow",0,0,flow_lpm=20,boundary_diameter_mm=25),Node("C1",x=100,y=0),
               Node("A1",x=220,y=-90),Node("B1",x=220,y=90),Node("A2",x=420,y=-90),Node("B2",x=420,y=90),
               Node("C2",x=540,y=0),Node("OUT","pressure",650,0,boundary_diameter_mm=25)],
        elements=[Element("FEED","IN","C1",length_m=2,fixed_flow_lpm=20),
                  Element("UPPER","A1","A2",length_m=8,fixed_flow_lpm=10),
                  Element("LOWER","B1","B2",length_m=8,fixed_flow_lpm=10),
                  Element("RETURN","C2","OUT",length_m=2,fixed_flow_lpm=20)],
        tees=[Tee("T1","C1","A1","B1",common_diameter_mm=25,run_diameter_mm=25,branch_diameter_mm=25,
                  k_run=0,k_branch=0,source="解析解検算のための理想・無損失分岐",fixed_run_lpm=10,fixed_branch_lpm=10),
              Tee("T2","C2","A2","B2",kind="merge",common_diameter_mm=25,run_diameter_mm=25,branch_diameter_mm=25,
                  k_run=0,k_branch=0,source="解析解検算のための理想・無損失合流",fixed_run_lpm=10,fixed_branch_lpm=10)],
        notes="理想分岐・合流（K=0）を明示指定。同じ抵抗の枝に10 L/minずつ流れる解析検算例。実際の継手のKではありません。")
    return p


def full_demo():
    def pipe(i,a,b,d,l,q):
        return Element(i,a,b,diameter_mm=d,outlet_diameter_mm=d,length_m=l,fixed_flow_lpm=q)
    p = Project(title="油配管：分岐・合流・径変化・フィルター（仮想デモ）",
        fluid=Fluid("仮想油（実機データではありません）",40,850,32),
        nodes=[Node("IN","flow",0,180,flow_lpm=30,boundary_diameter_mm=25),Node("S-C",x=130,y=180),
               Node("S-A",x=260,y=60),Node("S-B",x=260,y=300),Node("M-A",x=570,y=60),
               Node("F-OUT",x=410,y=300),Node("M-B",x=570,y=300),Node("M-C",x=700,y=180),
               Node("RED-IN",x=820,y=180),Node("RED-OUT",x=920,y=180),Node("EXP-IN",x=1050,y=180),
               Node("EXP-OUT",x=1150,y=180),Node("OUT","pressure",1300,180,boundary_diameter_mm=32)],
        elements=[pipe("FEED","IN","S-C",25,4,30),pipe("BRANCH-A","S-A","M-A",20,8,18),
                  Element("FILTER","S-B","F-OUT",kind="filter",diameter_mm=20,outlet_diameter_mm=20,
                          curve=[[0,0],[10,8],[20,20],[40,60]],source="仮想デモ用の曲線（メーカー値ではありません）",
                          allow_estimate=True,fixed_flow_lpm=12,bypass_kpa=100),
                  pipe("BRANCH-B","F-OUT","M-B",20,14,12),pipe("RETURN","M-C","RED-IN",25,3,30),
                  Element("CONTRACTION","RED-IN","RED-OUT",kind="contraction",diameter_mm=25,outlet_diameter_mm=16,
                          k=0.35,source="仮想K（実機の根拠ではありません）",allow_estimate=True,fixed_flow_lpm=30),
                  pipe("SMALL","RED-OUT","EXP-IN",16,2,30),
                  Element("EXPANSION","EXP-IN","EXP-OUT",kind="expansion",diameter_mm=16,outlet_diameter_mm=32,
                          allow_estimate=True,fixed_flow_lpm=30),pipe("OUTLET","EXP-OUT","OUT",32,3,30)],
        tees=[Tee("SPLIT","S-C","S-A","S-B",k_run=0.2,k_branch=0.8,
                  source="仮想K（実機の根拠ではありません）",allow_estimate=True,fixed_run_lpm=18,fixed_branch_lpm=12),
              Tee("MERGE","M-C","M-A","M-B",kind="merge",k_run=0.3,k_branch=0.6,
                  source="仮想K（実機の根拠ではありません）",allow_estimate=True,fixed_run_lpm=18,fixed_branch_lpm=12)],
        notes="全て仮想条件です。係数・物性・フィルター曲線は実設備の設計に使用しないでください。低Reの局所損失は概算を明示選択しています。出口は管断面のゲージ圧0 kPa。分岐流量は自動計算されます。")
    return p


def elbow_demo():
    angles=[0,45,90,180]
    return Project(title="エルボの角度比較（仮想・低Re概算）",fluid=Fluid("仮想油",40,850,32),
        nodes=[Node("IN","flow",0,100,flow_lpm=20,boundary_diameter_mm=25),
               Node("N1",x=200,y=100),Node("N2",x=400,y=100),Node("N3",x=600,y=100),
               Node("OUT","pressure",800,100,boundary_diameter_mm=25)],
        elements=[Element(f"E{i+1}",a,b,kind="elbow",roughness_mm=0,angle_deg=angle,
                          bend_radius_ratio=1.5,fixed_flow_lpm=20,allow_estimate=True)
                  for i,(a,b,angle) in enumerate(zip(["IN","N1","N2","N3"],["N1","N2","N3","OUT"],angles))],
        notes="仮想条件。0/45/90/180度、R/D=1.5を比較する操作例。低Reのため概算を明示選択。実機検証値ではありません。図の線の方向と計算角度は独立です。連続した曲管の干渉は扱いません。")


SAMPLES = {"全機能デモ（仮想）":full_demo,"対称分岐・合流の検算":symmetric,"直管の解析解検算":straight,
           "エルボ（0/45/90/180°・仮想）":elbow_demo}
